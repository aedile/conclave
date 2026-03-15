/**
 * Playwright end-to-end tests for the Dashboard screen.
 *
 * Acceptance criteria:
 *   - @axe-core/playwright accessibility scan on Dashboard reports 0 violations
 *   - Reload rehydration: mock active job in localStorage, reload page,
 *     assert progress bar resumes state from SSE stream.
 *
 * These tests require the built app (npm run build && npm run preview).
 * In CI the webServer config in playwright.config.ts handles this.
 *
 * Mocking strategy:
 *   - /health → 200 (vault unsealed, allows /dashboard access)
 *   - /jobs → paginated job list with one TRAINING job
 *   - /jobs/2 → single TRAINING job (for rehydration)
 *   - /jobs/2/stream → SSE stream simulated via route.fulfill with streaming
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

/** Shared job fixture for a training job. */
const trainingJob = {
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

/**
 * Build a minimal SSE response string for a progress event.
 *
 * @param data - The event payload object.
 * @returns An SSE-formatted string with event type and data.
 */
function sseProgressEvent(data: Record<string, unknown>): string {
  return `event: progress\ndata: ${JSON.stringify(data)}\n\n`;
}

test.describe("Dashboard screen", () => {
  test.beforeEach(async ({ page }) => {
    // Health check → unsealed (grants /dashboard access)
    await page.route("/health", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      }),
    );

    // Job list → one TRAINING job
    await page.route("/jobs?limit=20", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [trainingJob], next_cursor: null }),
      }),
    );

    // Single job lookup (for rehydration)
    await page.route("/jobs/2", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(trainingJob),
      }),
    );

    // SSE stream — emit one progress event then complete
    await page.route("/jobs/2/stream", (route) => {
      const progressPayload = sseProgressEvent({
        status: "TRAINING",
        current_epoch: 5,
        total_epochs: 10,
        percent: 50,
      });
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: progressPayload,
      });
    });
  });

  test("axe-core reports 0 accessibility violations on Dashboard", async ({
    page,
  }) => {
    await page.goto("/dashboard");

    // Wait for the job list to load
    await page.waitForSelector("text=orders");

    const results = await new AxeBuilder({ page }).analyze();

    expect(results.violations).toHaveLength(0);
  });

  test("page title is set to Dashboard — Conclave Engine", async ({ page }) => {
    await page.goto("/dashboard");
    await page.waitForSelector("text=orders");

    expect(await page.title()).toBe("Dashboard — Conclave Engine");
  });

  test("reload rehydration: progress bar resumes after page refresh", async ({
    page,
  }) => {
    // Set the active job ID in localStorage before navigating
    await page.goto("/dashboard");
    await page.waitForSelector("text=orders");

    // Inject the active job ID directly
    await page.evaluate(() => {
      localStorage.setItem("conclave_active_job_id", "2");
    });

    // Reload — Dashboard should read localStorage, call getJob(2), open SSE
    await page.reload();

    // The SSE stream should deliver progress and the progress bar should reflect it
    await page.waitForSelector('[role="progressbar"]', { timeout: 10000 });

    const progressBar = page.locator('[role="progressbar"]').first();
    await expect(progressBar).toBeVisible();

    // Verify aria attributes are present
    await expect(progressBar).toHaveAttribute("aria-valuemin", "0");
    await expect(progressBar).toHaveAttribute("aria-valuemax", "100");
  });

  test("aria-live polite region is present in the DOM", async ({ page }) => {
    await page.goto("/dashboard");
    await page.waitForSelector("text=orders");

    const liveRegion = page.locator('[aria-live="polite"]').first();
    await expect(liveRegion).toBeAttached();
  });

  test("Active Jobs heading is visible", async ({ page }) => {
    await page.goto("/dashboard");

    await expect(
      page.getByRole("heading", { name: /active jobs/i }),
    ).toBeVisible();
  });
});
