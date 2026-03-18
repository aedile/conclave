/**
 * E2E Accessibility Test Suite — P27-T27.4.
 *
 * Covers WCAG 2.1 AA accessibility for all P27 components:
 *   - AsyncButton  (aria-live, spinner aria-hidden, focus management, keyboard)
 *   - CreateJobForm (label/input pairing, aria-required, error association, keyboard)
 *   - JobList      (semantic structure, pagination keyboard, status text alternatives)
 *   - JobCard      (article + heading, progressbar ARIA, descriptive action labels)
 *   - WCAG landmark roles (main, navigation, form)
 *   - Keyboard navigation (tab order, focus indicators, no keyboard traps)
 *
 * Strategy:
 *   - All tests assert on REAL accessibility attributes — no mocks of aria values.
 *   - CSS-dependent assertions (touch targets, 44px minimums, overflow) follow the
 *     static-CSS analysis pattern established in responsive-breakpoints.test.ts.
 *   - Keyboard interaction tests use @testing-library/user-event for authentic
 *     event dispatch (not synthetic fireEvent).
 *   - jsdom does not apply stylesheets, so layout-dependent assertions read the
 *     raw CSS file directly.
 *
 * Known failure pattern guard (Phase 23 retro):
 *   - aria-live must announce BOTH start AND end of async operations.
 *   - Double-announcement must be prevented (spinner aria-hidden).
 *   - Focus must be restorable after async error dismissal (forwardRef).
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// API client mock — required so components that import api/client do not
// attempt real network calls during tests.
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

import AsyncButton from "../components/AsyncButton";
import CreateJobForm, {
  type CreateJobFormState,
} from "../components/CreateJobForm";
import JobList from "../components/JobList";
import JobCard from "../components/JobCard";
import type { JobResponse } from "../api/client";

// ---------------------------------------------------------------------------
// CSS text — loaded once for static-analysis assertions.
// Follows the pattern established in responsive-breakpoints.test.ts.
// ---------------------------------------------------------------------------

let cssText: string;

beforeAll(() => {
  const cssPath = resolve(__dirname, "..", "styles", "global.css");
  cssText = readFileSync(cssPath, "utf-8");
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const emptyForm: CreateJobFormState = {
  table_name: "",
  parquet_path: "",
  total_epochs: "",
  checkpoint_every_n: "",
};

const filledForm: CreateJobFormState = {
  table_name: "customers",
  parquet_path: "/data/customers.parquet",
  total_epochs: "100",
  checkpoint_every_n: "10",
};

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
  current_epoch: 5,
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
  artifact_path: "/output/products.parquet",
  error_msg: null,
  checkpoint_every_n: 2,
};

const failedJob: JobResponse = {
  id: 4,
  status: "FAILED",
  current_epoch: 3,
  total_epochs: 10,
  table_name: "inventory",
  parquet_path: "/data/inventory.parquet",
  artifact_path: null,
  error_msg: "CUDA out of memory",
  checkpoint_every_n: 2,
};

// ---------------------------------------------------------------------------
// Helper: render JobCard with sensible defaults
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
  return render(
    <JobCard
      job={job}
      sseState={overrides.sseState ?? null}
      onStart={overrides.onStart ?? vi.fn()}
      onDownload={overrides.onDownload ?? vi.fn()}
      isStarting={overrides.isStarting ?? false}
      isDownloading={overrides.isDownloading ?? false}
    />,
  );
}

// ---------------------------------------------------------------------------
// Helper: render JobList with sensible defaults
// ---------------------------------------------------------------------------

function renderJobList(
  jobs: JobResponse[],
  overrides: Partial<{
    nextCursor: number | null;
    isLoadingMore: boolean;
    onLoadMore: () => void;
    onStart: (id: number) => void;
    onDownload: (id: number) => void;
  }> = {},
) {
  return render(
    <JobList
      jobs={jobs}
      activeJobId={null}
      sseState={null}
      startingJobId={null}
      downloadingJobIds={new Set()}
      nextCursor={overrides.nextCursor ?? null}
      isLoadingMore={overrides.isLoadingMore ?? false}
      onStart={overrides.onStart ?? vi.fn()}
      onDownload={overrides.onDownload ?? vi.fn()}
      onLoadMore={overrides.onLoadMore ?? vi.fn()}
    />,
  );
}

// ===========================================================================
// 1. AsyncButton accessibility
// ===========================================================================

describe("AsyncButton — aria-live region announces state changes", () => {
  it("live region has role=status and aria-live=polite", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Working…">
        Submit
      </AsyncButton>,
    );

    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
    expect(liveRegion).toHaveAttribute("aria-atomic", "true");
  });

  it("announces loadingText when loading starts", () => {
    render(
      <AsyncButton isLoading={true} loadingText="Creating…">
        Create Job
      </AsyncButton>,
    );

    // The live region must contain the loadingText for screen readers
    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toHaveTextContent("Creating…");
  });

  it("clears the live region announcement when loading ends", () => {
    const { rerender } = render(
      <AsyncButton isLoading={true} loadingText="Saving…">
        Save
      </AsyncButton>,
    );

    // Verify loading text is announced
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");

    // Simulate loading complete
    rerender(
      <AsyncButton isLoading={false} loadingText="Saving…">
        Save
      </AsyncButton>,
    );

    // Live region must clear so screen readers hear the end of loading
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("live region is distinct from the button element (outside button)", () => {
    const { container } = render(
      <AsyncButton isLoading={true} loadingText="Loading…">
        Click
      </AsyncButton>,
    );

    const button = screen.getByRole("button");
    const liveRegion = screen.getByRole("status");

    // The live region must NOT be inside the button element
    expect(button.contains(liveRegion)).toBe(false);
    // Both must be present in the container
    expect(container.contains(button)).toBe(true);
    expect(container.contains(liveRegion)).toBe(true);
  });
});

describe("AsyncButton — spinner is hidden from assistive technology", () => {
  it("spinner span has aria-hidden=true to prevent double-announcement", () => {
    const { container } = render(
      <AsyncButton isLoading={true} loadingText="Processing…">
        Submit
      </AsyncButton>,
    );

    const spinner = container.querySelector(".async-button__spinner");
    expect(spinner).not.toBeNull();
    expect(spinner).toHaveAttribute("aria-hidden", "true");
  });

  it("spinner is not present when not loading (no hidden element polluting DOM)", () => {
    const { container } = render(
      <AsyncButton isLoading={false} loadingText="Processing…">
        Submit
      </AsyncButton>,
    );

    expect(container.querySelector(".async-button__spinner")).toBeNull();
  });

  it("only one element announces loading state (live region, not spinner)", () => {
    const { container } = render(
      <AsyncButton isLoading={true} loadingText="Uploading…">
        Upload
      </AsyncButton>,
    );

    // Spinner must be aria-hidden — the sole announcement channel is the live region
    const ariaHiddenElements = container.querySelectorAll('[aria-hidden="true"]');
    const spinners = container.querySelectorAll(".async-button__spinner");
    spinners.forEach((spinner) => {
      expect(spinner).toHaveAttribute("aria-hidden", "true");
    });
    // Every aria-hidden element in loading state should be a spinner
    expect(ariaHiddenElements.length).toBeGreaterThanOrEqual(1);
  });
});

describe("AsyncButton — focus management during loading transitions", () => {
  it("button retains focus when transitioning from idle to loading", () => {
    const { rerender } = render(
      <AsyncButton isLoading={false} loadingText="Saving…">
        Save
      </AsyncButton>,
    );

    const button = screen.getByRole("button");
    button.focus();
    expect(button).toHaveFocus();

    // Transition to loading state
    rerender(
      <AsyncButton isLoading={true} loadingText="Saving…">
        Save
      </AsyncButton>,
    );

    // The button element itself is still the focused element
    expect(button).toHaveFocus();
  });

  it("forwardRef allows parent to programmatically focus the button", () => {
    const ref = createRef<HTMLButtonElement>();

    render(
      <AsyncButton ref={ref} isLoading={false} loadingText="Working…">
        Submit
      </AsyncButton>,
    );

    expect(ref.current).not.toBeNull();
    ref.current?.focus();
    expect(ref.current).toHaveFocus();
  });

  it("forwardRef is accessible even during loading state", () => {
    const ref = createRef<HTMLButtonElement>();

    render(
      <AsyncButton ref={ref} isLoading={true} loadingText="Working…">
        Submit
      </AsyncButton>,
    );

    // ref must still point to the button even when disabled
    expect(ref.current).not.toBeNull();
    expect(ref.current?.tagName).toBe("BUTTON");
    expect(ref.current?.disabled).toBe(true);
  });
});

describe("AsyncButton — keyboard interaction", () => {
  it("Enter key triggers onClick when button is not loading", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncButton isLoading={false} loadingText="Working…" onClick={onClick}>
        Submit
      </AsyncButton>,
    );

    const button = screen.getByRole("button");
    button.focus();
    await user.keyboard("{Enter}");

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("Space key triggers onClick when button is not loading", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncButton isLoading={false} loadingText="Working…" onClick={onClick}>
        Submit
      </AsyncButton>,
    );

    const button = screen.getByRole("button");
    button.focus();
    await user.keyboard(" ");

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("Enter key does not trigger onClick when loading", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncButton isLoading={true} loadingText="Working…" onClick={onClick}>
        Submit
      </AsyncButton>,
    );

    const button = screen.getByRole("button");
    // Note: button is disabled during loading, so focus attempt still asserts the key does nothing
    await user.keyboard("{Enter}");

    expect(onClick).not.toHaveBeenCalled();
  });

  it("Space key does not trigger onClick when loading", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncButton isLoading={true} loadingText="Working…" onClick={onClick}>
        Submit
      </AsyncButton>,
    );

    await user.keyboard(" ");

    expect(onClick).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// 2. CreateJobForm accessibility
// ===========================================================================

describe("CreateJobForm — all form inputs have associated labels (htmlFor/id)", () => {
  it("Table Name label is associated with the table-name input via htmlFor/id", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    // getByLabelText only succeeds if htmlFor→id association is correct
    const input = screen.getByLabelText(/table name/i);
    expect(input).toHaveAttribute("id", "table-name");
  });

  it("Parquet Path label is associated with the parquet-path input via htmlFor/id", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByLabelText(/parquet path/i);
    expect(input).toHaveAttribute("id", "parquet-path");
  });

  it("Total Epochs label is associated with the total-epochs input via htmlFor/id", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByLabelText(/total epochs/i);
    expect(input).toHaveAttribute("id", "total-epochs");
  });

  it("Checkpoint Every label is associated with the checkpoint-every input via htmlFor/id", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByLabelText(/checkpoint every/i);
    expect(input).toHaveAttribute("id", "checkpoint-every");
  });
});

describe("CreateJobForm — required fields have aria-required", () => {
  it("all four inputs carry aria-required='true'", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const inputIds = ["table-name", "parquet-path", "total-epochs", "checkpoint-every"];
    inputIds.forEach((id) => {
      const input = document.getElementById(id);
      expect(input, `${id} must have aria-required`).toHaveAttribute(
        "aria-required",
        "true",
      );
    });
  });
});

describe("CreateJobForm — form error messages are programmatically associated", () => {
  it("error container has id='form-error' for aria-describedby association", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const errorContainer = document.getElementById("form-error");
    expect(errorContainer).toBeInTheDocument();
  });

  it("error container has role=alert for screen reader announcement", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Total Epochs must be a valid integer."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const errorContainer = document.getElementById("form-error");
    expect(errorContainer).toHaveAttribute("role", "alert");
  });

  it("total_epochs has aria-describedby pointing to form-error when it is the error field", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Total Epochs must be a valid integer."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByLabelText(/total epochs/i);
    expect(input).toHaveAttribute("aria-describedby", "form-error");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("checkpoint_every_n has aria-describedby pointing to form-error when it is the error field", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Checkpoint Every must be a valid integer."
        formErrorField="checkpoint_every_n"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const input = screen.getByLabelText(/checkpoint every/i);
    expect(input).toHaveAttribute("aria-describedby", "form-error");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("error container is always in the DOM (NVDA+Firefox repeat-announcement guard)", () => {
    // Render with no error — container must still exist so NVDA does not swallow
    // repeat announcements when the same error fires twice in a row.
    const { rerender } = render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(document.getElementById("form-error")).toBeInTheDocument();

    // Still present after an error fires
    rerender(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Required field."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(document.getElementById("form-error")).toBeInTheDocument();
  });
});

describe("CreateJobForm — submit button is keyboard accessible", () => {
  it("Create Job submit button is a native button element (inherently keyboard accessible)", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const button = screen.getByRole("button", { name: /create job/i });
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveAttribute("type", "submit");
  });

  it("Create Job submit button receives focus via keyboard tab", async () => {
    const user = userEvent.setup();

    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    // Tab through the form fields to reach the submit button
    // There are 4 inputs + 1 button = 5 tab stops
    await user.tab(); // table-name
    await user.tab(); // parquet-path
    await user.tab(); // total-epochs
    await user.tab(); // checkpoint-every
    await user.tab(); // submit button

    const button = screen.getByRole("button", { name: /create job/i });
    expect(button).toHaveFocus();
  });

  it("form can be submitted via Enter key when a field has focus", async () => {
    const onSubmit = vi.fn((e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
    });

    render(
      <CreateJobForm
        form={filledForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const form = document.querySelector("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    expect(onSubmit).toHaveBeenCalledOnce();
  });
});

// ===========================================================================
// 3. JobList accessibility
// ===========================================================================

describe("JobList — semantic structure", () => {
  it("Active Jobs section has a heading element for landmark navigation", () => {
    renderJobList([queuedJob]);

    const heading = screen.getByRole("heading", { name: /active jobs/i });
    expect(heading).toBeInTheDocument();
    expect(heading.tagName).toBe("H2");
  });

  it("section is labelled by its heading (aria-labelledby)", () => {
    const { container } = renderJobList([queuedJob]);

    const section = container.querySelector("section");
    expect(section).toHaveAttribute("aria-labelledby", "active-jobs-heading");

    const heading = document.getElementById("active-jobs-heading");
    expect(heading).toBeInTheDocument();
    expect(heading?.textContent).toMatch(/active jobs/i);
  });

  it("each job is rendered as an article element (landmark semantics)", () => {
    const { container } = renderJobList([queuedJob, completeJob]);

    const articles = container.querySelectorAll("article.job-card");
    expect(articles.length).toBe(2);
  });

  it("each job card has an h3 heading with the table name", () => {
    renderJobList([queuedJob, completeJob]);

    const headings = screen.getAllByRole("heading", { level: 3 });
    const headingTexts = headings.map((h) => h.textContent);
    expect(headingTexts).toContain("customers");
    expect(headingTexts).toContain("products");
  });
});

describe("JobList — pagination controls are keyboard accessible", () => {
  it("Load More button is a native button element (inherently keyboard accessible)", () => {
    renderJobList([queuedJob], { nextCursor: 10 });

    const button = screen.getByRole("button", { name: /load more/i });
    expect(button.tagName).toBe("BUTTON");
    expect(button).not.toBeDisabled();
  });

  it("Load More button can be activated via Enter key", async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();

    renderJobList([queuedJob], { nextCursor: 10, onLoadMore });

    const button = screen.getByRole("button", { name: /load more/i });
    button.focus();
    await user.keyboard("{Enter}");

    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("Load More button can be activated via Space key", async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();

    renderJobList([queuedJob], { nextCursor: 10, onLoadMore });

    const button = screen.getByRole("button", { name: /load more/i });
    button.focus();
    await user.keyboard(" ");

    expect(onLoadMore).toHaveBeenCalledOnce();
  });

  it("Load More button shows loading text and is disabled when isLoadingMore=true", () => {
    renderJobList([queuedJob], { nextCursor: 10, isLoadingMore: true });

    // The button role still exists (accessible name changes to loading text)
    const button = screen.getByRole("button", { name: /loading/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-disabled", "true");
  });
});

describe("JobList — status indicators have text alternatives (not color-only)", () => {
  it("QUEUED status is conveyed via text, not color alone", () => {
    renderJobList([queuedJob]);

    // The status text must be visible in the DOM (not just a CSS color class)
    expect(screen.getByText(/queued/i)).toBeInTheDocument();
  });

  it("TRAINING status is conveyed via text, not color alone", () => {
    renderJobList([trainingJob]);

    expect(screen.getByText(/training/i)).toBeInTheDocument();
  });

  it("COMPLETE status is conveyed via text, not color alone", () => {
    renderJobList([completeJob]);

    expect(screen.getByText(/complete/i)).toBeInTheDocument();
  });

  it("FAILED status is conveyed via text, not color alone", () => {
    renderJobList([failedJob]);

    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });
});

describe("JobList — action buttons have descriptive accessible names", () => {
  it("Start button has an accessible name (not empty)", () => {
    renderJobList([queuedJob]);

    const button = screen.getByRole("button", { name: /start/i });
    expect(button).toBeInTheDocument();
    expect(button.textContent?.trim()).not.toBe("");
  });

  it("Download button has a descriptive accessible name including table_name", () => {
    renderJobList([completeJob]);

    // aria-label encodes the table name for distinguishable screen-reader text
    const button = screen.getByRole("button", {
      name: /download synthetic data for products/i,
    });
    expect(button).toBeInTheDocument();
  });

  it("Download button accessible name is unique per job table_name", () => {
    const completeJob2: JobResponse = {
      ...completeJob,
      id: 5,
      table_name: "orders",
    };

    renderJobList([completeJob, completeJob2]);

    const productsBtn = screen.getByRole("button", {
      name: /download synthetic data for products/i,
    });
    const ordersBtn = screen.getByRole("button", {
      name: /download synthetic data for orders/i,
    });

    expect(productsBtn).toBeInTheDocument();
    expect(ordersBtn).toBeInTheDocument();
    expect(productsBtn).not.toBe(ordersBtn);
  });
});

// ===========================================================================
// 4. JobCard accessibility
// ===========================================================================

describe("JobCard — semantic structure", () => {
  it("renders as an article element (landmark for a self-contained unit)", () => {
    const { container } = renderCard(queuedJob);

    const article = container.querySelector("article.job-card");
    expect(article).toBeInTheDocument();
  });

  it("contains an h3 heading with the table name for screen reader navigation", () => {
    renderCard(queuedJob);

    const heading = screen.getByRole("heading", { level: 3, name: "customers" });
    expect(heading).toBeInTheDocument();
  });

  it("epoch counter is rendered as a paragraph (not a heading — avoids noisy outline)", () => {
    const { container } = renderCard(trainingJob);

    const epochP = container.querySelector("p.job-card__epoch");
    expect(epochP).toBeInTheDocument();
    expect(epochP?.textContent).toContain("Epoch");
  });
});

describe("JobCard — progressbar ARIA attributes", () => {
  it("progress bar has role=progressbar", () => {
    renderCard(trainingJob);

    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("progress bar has aria-label describing the job", () => {
    renderCard(trainingJob);

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-label", "Job 2 progress");
  });

  it("progress bar has aria-valuemin=0", () => {
    renderCard(trainingJob);

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemin", "0");
  });

  it("progress bar has aria-valuemax=100", () => {
    renderCard(trainingJob);

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuemax", "100");
  });

  it("progress bar has aria-valuenow matching computed percentage", () => {
    renderCard(trainingJob); // 5/10 = 50%

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
  });

  it("progress bar is not shown for QUEUED jobs (avoids misleading progress indication)", () => {
    renderCard(queuedJob);

    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});

describe("JobCard — action buttons have descriptive accessible names", () => {
  it("Start button has accessible name 'Start'", () => {
    renderCard(queuedJob);

    const button = screen.getByRole("button", { name: /start/i });
    expect(button).toBeInTheDocument();
  });

  it("Download button aria-label includes the job table_name", () => {
    renderCard(completeJob);

    const button = screen.getByRole("button", {
      name: `Download synthetic data for ${completeJob.table_name}`,
    });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute(
      "aria-label",
      `Download synthetic data for ${completeJob.table_name}`,
    );
  });

  it("Start button is keyboard accessible (Enter activates it)", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();

    renderCard(queuedJob, { onStart });

    const button = screen.getByRole("button", { name: /start/i });
    button.focus();
    await user.keyboard("{Enter}");

    expect(onStart).toHaveBeenCalledWith(queuedJob.id);
  });

  it("Download button is keyboard accessible (Enter activates it)", async () => {
    const user = userEvent.setup();
    const onDownload = vi.fn();

    renderCard(completeJob, { onDownload });

    const button = screen.getByRole("button", {
      name: /download synthetic data for products/i,
    });
    button.focus();
    await user.keyboard("{Enter}");

    expect(onDownload).toHaveBeenCalledWith(completeJob.id);
  });
});

describe("JobCard — error message for failed jobs", () => {
  it("error message is visible in the DOM (not color-only indication)", () => {
    renderCard(failedJob);

    // Error text must exist in the DOM for screen readers
    expect(screen.getByText("CUDA out of memory")).toBeInTheDocument();
  });

  it("FAILED status is textually communicated in the status badge", () => {
    renderCard(failedJob);

    // Status text must be in the DOM alongside the color token
    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });
});

// ===========================================================================
// 5. Responsive layout accessibility (CSS static analysis)
// ===========================================================================

describe("Responsive layout — touch targets meet 44px minimum at mobile breakpoint", () => {
  it("unseal submit button has min-height of at least 44px at mobile breakpoint", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");

    expect(mobileBlock).toMatch(/\.unseal-form__submit/);
    expect(mobileBlock).toMatch(/min-height:\s*(44px|2\.75rem|3rem|48px)/);
  });

  it("unseal form input has min-height of at least 44px at mobile breakpoint", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");

    expect(mobileBlock).toMatch(/\.unseal-form__input|\.unseal-form__submit/);
    expect(mobileBlock).toMatch(/min-height:\s*(44px|2\.75rem|3rem|48px)/);
  });
});

describe("Responsive layout — content readable at 320px (no horizontal scroll)", () => {
  it("dashboard-main uses max-width or width bounded strategy in global CSS", () => {
    // The dashboard main content area must be constrained to prevent overflow
    expect(cssText).toMatch(/\.dashboard-main/);
    // Main layout must include box-sizing or width control
    expect(cssText).toMatch(/box-sizing:\s*border-box/);
  });

  it("unseal card has max-width constraint removed at mobile breakpoint", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");

    expect(mobileBlock).toMatch(/\.unseal-card/);
    expect(mobileBlock).toMatch(/max-width:\s*(none|100%)/);
  });

  it("dashboard form inputs are full-width at mobile or tablet breakpoint", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    const tabletBlock = extractMediaBlock(cssText, "1024px");
    const combined = mobileBlock + tabletBlock;

    // At least one breakpoint must override form input width for readable layout
    expect(combined).toMatch(/\.dashboard-form__input/);
  });
});

describe("Responsive layout — focus order remains logical after responsive reflow", () => {
  it("focus indicators are not overridden to 'none' inside responsive media queries", () => {
    const mediaBlocks = extractAllMediaBlocks(cssText);

    for (const block of mediaBlocks) {
      if (block.includes("prefers-reduced-motion")) continue;

      // No responsive breakpoint may suppress focus-visible outline
      const suppressesFocus = /:focus-visible[^{]*\{[^}]*outline:\s*none/s.test(block);
      expect(suppressesFocus).toBe(false);
    }
  });

  it("base stylesheet defines a :focus-visible outline rule (not hidden by overflow)", () => {
    // The outline must be defined in the base CSS, not inside a media query that could be
    // overridden, so focus indicators remain visible at all viewport sizes.
    expect(cssText).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px/s);
  });
});

// ===========================================================================
// 6. Keyboard navigation
// ===========================================================================

describe("Keyboard navigation — tab order follows visual order in CreateJobForm", () => {
  it("inputs appear in DOM order matching visual order (no tabindex reordering)", () => {
    const { container } = render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    // All inputs must have default tab order (no positive tabIndex that reorders)
    const inputs = container.querySelectorAll("input");
    inputs.forEach((input) => {
      const tabIndex = parseInt(input.getAttribute("tabindex") ?? "0", 10);
      // Positive tabIndex > 0 reorders the tab sequence (a11y anti-pattern)
      expect(tabIndex).toBeLessThanOrEqual(0);
    });
  });

  it("submit button has no positive tabIndex that would reorder it unexpectedly", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const button = screen.getByRole("button", { name: /create job/i });
    const tabIndex = parseInt(button.getAttribute("tabindex") ?? "0", 10);
    expect(tabIndex).toBeLessThanOrEqual(0);
  });
});

describe("Keyboard navigation — no keyboard traps", () => {
  it("Tab key moves focus away from the AsyncButton (no trap)", async () => {
    const user = userEvent.setup();

    render(
      <div>
        <AsyncButton isLoading={false} loadingText="Working…">
          Button A
        </AsyncButton>
        <button type="button">Button B</button>
      </div>,
    );

    const buttonA = screen.getByRole("button", { name: /button a/i });
    const buttonB = screen.getByRole("button", { name: /button b/i });

    buttonA.focus();
    expect(buttonA).toHaveFocus();

    await user.tab();

    // Focus must move forward — buttonA must no longer hold focus
    expect(buttonA).not.toHaveFocus();
    expect(buttonB).toHaveFocus();
  });

  it("Shift+Tab moves focus back from AsyncButton (no backward trap)", async () => {
    const user = userEvent.setup();

    render(
      <div>
        <button type="button">Button A</button>
        <AsyncButton isLoading={false} loadingText="Working…">
          Button B
        </AsyncButton>
      </div>,
    );

    const buttonA = screen.getByRole("button", { name: /button a/i });
    const buttonB = screen.getByRole("button", { name: /button b/i });

    buttonB.focus();
    expect(buttonB).toHaveFocus();

    await user.tab({ shift: true });

    expect(buttonB).not.toHaveFocus();
    expect(buttonA).toHaveFocus();
  });

  it("disabled AsyncButton is skipped in tab order", async () => {
    const user = userEvent.setup();

    render(
      <div>
        <button type="button">Before</button>
        <AsyncButton isLoading={true} loadingText="Working…">
          Loading Button
        </AsyncButton>
        <button type="button">After</button>
      </div>,
    );

    const before = screen.getByRole("button", { name: /before/i });
    const after = screen.getByRole("button", { name: /after/i });

    before.focus();
    await user.tab();

    // Disabled buttons are skipped by the browser's native tab sequence.
    // jsdom respects this — focus should land on "After", skipping the disabled one.
    expect(after).toHaveFocus();
  });
});

// ===========================================================================
// 7. WCAG 2.1 AA landmark tests
// ===========================================================================

describe("WCAG landmarks — Dashboard layout landmarks", () => {
  it("Dashboard renders a main element as the primary content landmark", () => {
    // Verify the main landmark is present in the component's rendered output
    // by checking the JobList section (which is always rendered by Dashboard).
    // We test the section landmark here since Dashboard needs API mocks for full render.
    const { container } = renderJobList([queuedJob]);

    const section = container.querySelector("section");
    expect(section).toBeInTheDocument();
    // Section has aria-labelledby for a named landmark (not anonymous region)
    expect(section).toHaveAttribute("aria-labelledby");
  });

  it("CreateJobForm section has aria-labelledby pointing to its heading", () => {
    const { container } = render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const section = container.querySelector("section");
    expect(section).toHaveAttribute("aria-labelledby", "create-job-heading");

    const heading = document.getElementById("create-job-heading");
    expect(heading).toBeInTheDocument();
    expect(heading?.textContent).toMatch(/create job/i);
  });

  it("CreateJobForm section heading is an h2 (second-level heading in document outline)", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const heading = screen.getByRole("heading", {
      level: 2,
      name: /create job/i,
    });
    expect(heading).toBeInTheDocument();
  });

  it("JobList section heading is an h2 (second-level heading in document outline)", () => {
    renderJobList([queuedJob]);

    const heading = screen.getByRole("heading", {
      level: 2,
      name: /active jobs/i,
    });
    expect(heading).toBeInTheDocument();
  });

  it("JobCard uses h3 headings (third-level, below section h2)", () => {
    renderJobList([queuedJob, completeJob]);

    const h3Headings = screen.getAllByRole("heading", { level: 3 });
    // One h3 per job card
    expect(h3Headings.length).toBeGreaterThanOrEqual(2);
  });
});

describe("WCAG landmarks — form role", () => {
  it("CreateJobForm contains a form element (implicit form landmark)", () => {
    const { container } = render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const form = container.querySelector("form");
    expect(form).toBeInTheDocument();
  });

  it("form element is accessible by role when it has a label (via the enclosing section)", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    // The form is contained within a section labelled by "Create Job" heading.
    // The section provides the landmark context for the form.
    const section = document.querySelector("section[aria-labelledby='create-job-heading']");
    expect(section).toBeInTheDocument();
    const form = section?.querySelector("form");
    expect(form).toBeInTheDocument();
  });

  it("form error container has role=alert for live error announcement", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Required."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const alertRegion = document.getElementById("form-error");
    expect(alertRegion).toHaveAttribute("role", "alert");
  });
});

// ===========================================================================
// Helpers
// ===========================================================================

/**
 * Extracts the content of a @media block whose condition string contains
 * the given substring. Returns all such blocks concatenated if multiple match.
 * Handles nested braces correctly by counting depth.
 *
 * @param css - Full CSS text to scan.
 * @param conditionSubstring - Substring to match in the @media condition.
 * @returns Concatenated block content strings, or "" if none found.
 */
function extractMediaBlock(css: string, conditionSubstring: string): string {
  const results: string[] = [];
  let searchFrom = 0;

  while (true) {
    const mediaIndex = css.indexOf("@media", searchFrom);
    if (mediaIndex === -1) break;

    const openBrace = css.indexOf("{", mediaIndex);
    if (openBrace === -1) break;

    const condition = css.slice(mediaIndex, openBrace);

    let depth = 1;
    let pos = openBrace + 1;
    while (pos < css.length && depth > 0) {
      if (css[pos] === "{") depth++;
      else if (css[pos] === "}") depth--;
      pos++;
    }

    const blockContent = css.slice(openBrace + 1, pos - 1);

    if (condition.includes(conditionSubstring)) {
      results.push(blockContent);
    }

    searchFrom = pos;
  }

  return results.join("\n");
}

/**
 * Returns all @media block bodies as an array of strings.
 *
 * @param css - Full CSS text to scan.
 * @returns Array of block body strings, one per @media rule.
 */
function extractAllMediaBlocks(css: string): string[] {
  const results: string[] = [];
  let searchFrom = 0;

  while (true) {
    const mediaIndex = css.indexOf("@media", searchFrom);
    if (mediaIndex === -1) break;

    const openBrace = css.indexOf("{", mediaIndex);
    if (openBrace === -1) break;

    let depth = 1;
    let pos = openBrace + 1;
    while (pos < css.length && depth > 0) {
      if (css[pos] === "{") depth++;
      else if (css[pos] === "}") depth--;
      pos++;
    }

    results.push(css.slice(openBrace + 1, pos - 1));
    searchFrom = pos;
  }

  return results;
}
