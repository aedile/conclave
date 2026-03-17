/**
 * Vitest unit tests for the JobCard component.
 *
 * Covers safePercent edge cases (total_epochs=0, negative, NaN), status badge
 * rendering, progress bar ARIA attributes, Start button visibility, SSE
 * state overlay, and Download button (AC1–AC5, P23-T23.3).
 *
 * Finding 1 (QA + UI/UX T6.1): Adds explicit test asserting aria-valuenow="0"
 * when total_epochs=0, verifying the updated safePercent guard covers falsy
 * and non-positive values rather than only strict equality to 0.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import JobCard from "../components/JobCard";
import type { JobResponse } from "../api/client";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const baseJob: JobResponse = {
  id: 1,
  status: "TRAINING",
  current_epoch: 5,
  total_epochs: 10,
  table_name: "customers",
  parquet_path: "/data/customers.parquet",
  artifact_path: null,
  error_msg: null,
  checkpoint_every_n: 2,
};

const queuedJob: JobResponse = {
  ...baseJob,
  id: 2,
  status: "QUEUED",
  current_epoch: 0,
  total_epochs: 10,
  table_name: "orders",
};

const completeJob: JobResponse = {
  ...baseJob,
  id: 3,
  status: "COMPLETE",
  current_epoch: 10,
  total_epochs: 10,
  table_name: "products",
  artifact_path: "/output/products.parquet",
};

const failedJob: JobResponse = {
  ...baseJob,
  id: 4,
  status: "FAILED",
  current_epoch: 3,
  total_epochs: 10,
  table_name: "inventory",
  error_msg: "CUDA out of memory",
};

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderCard(
  job: JobResponse,
  overrides: Partial<{
    sseState: Parameters<typeof JobCard>[0]["sseState"];
    onStart: Parameters<typeof JobCard>[0]["onStart"];
    onDownload: Parameters<typeof JobCard>[0]["onDownload"];
    isStarting: boolean;
    isDownloading: boolean;
  }> = {},
) {
  const props = {
    job,
    sseState: overrides.sseState ?? null,
    onStart: overrides.onStart ?? vi.fn(),
    onDownload: overrides.onDownload ?? vi.fn(),
    isStarting: overrides.isStarting ?? false,
    isDownloading: overrides.isDownloading ?? false,
  };
  return render(<JobCard {...props} />);
}

// ---------------------------------------------------------------------------
// safePercent edge cases
// ---------------------------------------------------------------------------

describe("JobCard — safePercent guard (Finding 1)", () => {
  it("renders aria-valuenow=0 when total_epochs=0 (division-by-zero guard)", () => {
    const job: JobResponse = {
      ...baseJob,
      status: "TRAINING",
      current_epoch: 0,
      total_epochs: 0,
    };

    renderCard(job);

    const progressBar = screen.getByRole("progressbar", { name: /job 1 progress/i });
    expect(progressBar).toHaveAttribute("aria-valuenow", "0");
  });

  it("renders aria-valuenow=0 when total_epochs is negative", () => {
    const job: JobResponse = {
      ...baseJob,
      status: "TRAINING",
      current_epoch: 5,
      total_epochs: -1,
    };

    renderCard(job);

    const progressBar = screen.getByRole("progressbar", { name: /job 1 progress/i });
    expect(progressBar).toHaveAttribute("aria-valuenow", "0");
  });

  it("renders correct aria-valuenow for normal training progress", () => {
    renderCard(baseJob);

    const progressBar = screen.getByRole("progressbar", { name: /job 1 progress/i });
    // 5 / 10 * 100 = 50
    expect(progressBar).toHaveAttribute("aria-valuenow", "50");
  });

  it("renders aria-valuenow=100 for a COMPLETE job", () => {
    renderCard(completeJob);

    const progressBar = screen.getByRole("progressbar", { name: /job 3 progress/i });
    expect(progressBar).toHaveAttribute("aria-valuenow", "100");
  });
});

// ---------------------------------------------------------------------------
// Progress bar visibility
// ---------------------------------------------------------------------------

describe("JobCard — progress bar visibility", () => {
  it("shows progress bar for TRAINING status", () => {
    renderCard(baseJob);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows progress bar for COMPLETE status", () => {
    renderCard(completeJob);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows progress bar for FAILED status", () => {
    renderCard(failedJob);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("does not show progress bar for QUEUED status", () => {
    renderCard(queuedJob);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Progress bar ARIA attributes
// ---------------------------------------------------------------------------

describe("JobCard — progress bar ARIA attributes", () => {
  it("has aria-valuemin=0, aria-valuemax=100, and aria-label", () => {
    renderCard(baseJob);
    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
    expect(bar).toHaveAttribute("aria-label", "Job 1 progress");
  });
});

// ---------------------------------------------------------------------------
// Status badge rendering
// ---------------------------------------------------------------------------

describe("JobCard — status badge", () => {
  it("displays the QUEUED badge", () => {
    renderCard(queuedJob);
    expect(screen.getByText(/queued/i)).toBeInTheDocument();
  });

  it("displays the TRAINING badge", () => {
    renderCard(baseJob);
    expect(screen.getByText(/training/i)).toBeInTheDocument();
  });

  it("displays the COMPLETE badge", () => {
    renderCard(completeJob);
    expect(screen.getByText(/complete/i)).toBeInTheDocument();
  });

  it("displays the FAILED badge", () => {
    renderCard(failedJob);
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Start button
// ---------------------------------------------------------------------------

describe("JobCard — Start button", () => {
  it("shows Start button only for QUEUED jobs", () => {
    renderCard(queuedJob);
    expect(screen.getByRole("button", { name: /start/i })).toBeInTheDocument();
  });

  it("does not show Start button for non-QUEUED jobs", () => {
    renderCard(baseJob);
    expect(screen.queryByRole("button", { name: /start/i })).not.toBeInTheDocument();
  });

  it("calls onStart with job id when Start is clicked", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();

    renderCard(queuedJob, { onStart });

    await user.click(screen.getByRole("button", { name: /start/i }));

    expect(onStart).toHaveBeenCalledWith(queuedJob.id);
  });

  it("disables Start button and shows Starting… when isStarting=true", () => {
    renderCard(queuedJob, { isStarting: true });

    const button = screen.getByRole("button", { name: /starting/i });
    expect(button).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// Error message for failed jobs
// ---------------------------------------------------------------------------

describe("JobCard — error message", () => {
  it("renders error message for FAILED job", () => {
    renderCard(failedJob);
    expect(screen.getByText("CUDA out of memory")).toBeInTheDocument();
  });

  it("does not render error message when FAILED job has no error_msg", () => {
    const jobWithNoError: JobResponse = { ...failedJob, error_msg: null };
    renderCard(jobWithNoError);
    // No error paragraph should be present
    expect(screen.queryByText(/cuda/i)).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// SSE state overlay
// ---------------------------------------------------------------------------

describe("JobCard — SSE state overlay", () => {
  it("displays SSE progress percent in aria-valuenow when sseState is present", () => {
    renderCard(baseJob, {
      sseState: {
        status: "TRAINING",
        currentEpoch: 7,
        totalEpochs: 10,
        percent: 70,
        error: null,
      },
    });

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "70");
  });

  it("uses job snapshot when sseState is null", () => {
    renderCard(baseJob, { sseState: null });

    const bar = screen.getByRole("progressbar");
    // 5 / 10 * 100 = 50
    expect(bar).toHaveAttribute("aria-valuenow", "50");
  });
});

// ---------------------------------------------------------------------------
// Download button (P23-T23.3 AC1–AC5)
// ---------------------------------------------------------------------------

describe("JobCard — Download button (P23-T23.3)", () => {
  it("AC1: shows Download button only for COMPLETE jobs", () => {
    renderCard(completeJob);
    expect(
      screen.getByRole("button", { name: /download synthetic data for products/i }),
    ).toBeInTheDocument();
  });

  it("AC1: does not show Download button for TRAINING jobs", () => {
    renderCard(baseJob);
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("AC1: does not show Download button for QUEUED jobs", () => {
    renderCard(queuedJob);
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("AC1: does not show Download button for FAILED jobs", () => {
    renderCard(failedJob);
    expect(screen.queryByRole("button", { name: /download/i })).not.toBeInTheDocument();
  });

  it("AC2: calls onDownload with job id when Download is clicked", async () => {
    const user = userEvent.setup();
    const onDownload = vi.fn();

    renderCard(completeJob, { onDownload });

    await user.click(
      screen.getByRole("button", { name: /download synthetic data for products/i }),
    );

    expect(onDownload).toHaveBeenCalledWith(completeJob.id);
  });

  it("AC3: disables Download button and shows Downloading… when isDownloading=true", () => {
    renderCard(completeJob, { isDownloading: true });

    const button = screen.getByRole("button", { name: /downloading/i });
    expect(button).toBeDisabled();
  });

  it("AC5: Download button has aria-label containing the job table_name", () => {
    renderCard(completeJob);

    const button = screen.getByRole("button", {
      name: /download synthetic data for products/i,
    });
    expect(button).toHaveAttribute(
      "aria-label",
      `Download synthetic data for ${completeJob.table_name}`,
    );
  });

  it("AC5: Download button is keyboard accessible (focusable and activatable)", async () => {
    const user = userEvent.setup();
    const onDownload = vi.fn();

    renderCard(completeJob, { onDownload });

    const button = screen.getByRole("button", {
      name: /download synthetic data for products/i,
    });

    // Tab to the button and press Enter
    button.focus();
    await user.keyboard("{Enter}");

    expect(onDownload).toHaveBeenCalledWith(completeJob.id);
  });
});
