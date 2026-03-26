/**
 * Playwright end-to-end tests for the Download button on COMPLETE job cards.
 *
 * Acceptance criteria (P23-T23.3):
 *   AC1 — Download button visible only on COMPLETE jobs.
 *   AC2 — Clicking Download triggers GET /jobs/{id}/download as a browser download.
 *   AC3 — Button disabled while download in progress.
 *   AC4 — Error toast shown on download failure.
 *   AC5 — WCAG 2.1 AA: keyboard accessible, aria-label.
 *
 * Mocking strategy:
 *   - /health → 200 (vault unsealed, grants /dashboard access)
 *   - /jobs?limit=20 → job list with one COMPLETE job
 *   - GET /jobs/{id}/download → octet-stream blob
 *
 * CI NOTE: Playwright e2e tests require browser binaries and a running
 * preview server. They are run locally per the local-CI policy until
 * a dedicated e2e CI job is established (see CLAUDE.md budget note).
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A COMPLETE job — shows the Download button. */
const completeJob = {
  id: 7,
  status: "COMPLETE",
  current_epoch: 10,
  total_epochs: 10,
  table_name: "reports",
  parquet_path: "/data/reports.parquet",
  artifact_path: "/output/reports_synth.parquet",
  error_msg: null,
  checkpoint_every_n: 2,
};

/** A TRAINING job — must NOT show the Download button. */
const trainingJob = {
  id: 8,
  status: "TRAINING",
  current_epoch: 5,
  total_epochs: 10,
  table_name: "metrics",
  parquet_path: "/data/metrics.parquet",
  artifact_path: null,
  error_msg: null,
  checkpoint_every_n: 2,
};

// ---------------------------------------------------------------------------
// Route helper
// ---------------------------------------------------------------------------

type PageType = Parameters<Parameters<typeof test>[1]>[0]["page"];

/**
 * Register the standard health + job-list routes for the download test suite.
 *
 * @param page - The Playwright Page instance.
 * @param jobs - Job list items to return from GET /jobs.
 */
async function mockBaseRoutes(
  page: PageType,
  jobs: unknown[],
): Promise<void> {
  await page.route("/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    }),
  );

  await page.route("/api/v1/jobs?limit=20", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: jobs, next_cursor: null }),
    }),
  );
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

test.describe("Download button — COMPLETE job card (P23-T23.3)", () => {
  test("AC1: Download button is visible on a COMPLETE job card", async ({ page }) => {
    await mockBaseRoutes(page, [completeJob]);

    await page.goto("/dashboard");
    await page.waitForSelector("text=reports");

    await expect(
      page.getByRole("button", {
        name: /download synthetic data for reports/i,
      }),
    ).toBeVisible();
  });

  test("AC1: Download button is NOT visible on a TRAINING job card", async ({
    page,
  }) => {
    await mockBaseRoutes(page, [trainingJob]);

    await page.goto("/dashboard");
    await page.waitForSelector("text=metrics");

    await expect(
      page.getByRole("button", { name: /download/i }),
    ).not.toBeVisible();
  });

  test("AC2: clicking Download triggers GET /jobs/{id}/download", async ({
    page,
  }) => {
    await mockBaseRoutes(page, [completeJob]);

    // Mock the download endpoint — return a minimal octet-stream blob
    await page.route("/api/v1/jobs/7/download", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/octet-stream",
        headers: {
          "Content-Disposition": 'attachment; filename="reports_synth.parquet"',
        },
        body: Buffer.from("parquet-binary-data"),
      }),
    );

    await page.goto("/dashboard");
    await page.waitForSelector("text=reports");

    // Intercept the download request to verify it was made
    const [downloadRequest] = await Promise.all([
      page.waitForRequest((req) => req.url().includes("/api/v1/jobs/7/download")),
      page
        .getByRole("button", { name: /download synthetic data for reports/i })
        .click(),
    ]);

    expect(downloadRequest.url()).toContain("/api/v1/jobs/7/download");
    expect(downloadRequest.method()).toBe("GET");
  });

  test("AC4: error toast appears when download endpoint returns 500", async ({
    page,
  }) => {
    await mockBaseRoutes(page, [completeJob]);

    // Mock the download endpoint to fail with RFC 7807 error
    await page.route("/api/v1/jobs/7/download", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/problem+json",
        body: JSON.stringify({
          type: "about:blank",
          title: "Internal Server Error",
          status: 500,
          detail: "Artifact not found on disk.",
        }),
      }),
    );

    await page.goto("/dashboard");
    await page.waitForSelector("text=reports");

    await page
      .getByRole("button", { name: /download synthetic data for reports/i })
      .click();

    // Error toast should appear (role=alertdialog from RFC7807Toast)
    await expect(page.getByRole("alertdialog")).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("alertdialog")).toContainText(
      /internal server error/i,
    );
  });

  test("AC5: axe-core reports 0 accessibility violations on job completion view with Download button", async ({
    page,
  }) => {
    await mockBaseRoutes(page, [completeJob]);

    await page.goto("/dashboard");
    await page.waitForSelector("text=reports");

    // Confirm the button is present before running axe
    await expect(
      page.getByRole("button", {
        name: /download synthetic data for reports/i,
      }),
    ).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    expect(results.violations).toHaveLength(0);
  });

  test("AC5: Download button has correct aria-label", async ({ page }) => {
    await mockBaseRoutes(page, [completeJob]);

    await page.goto("/dashboard");
    await page.waitForSelector("text=reports");

    const btn = page.getByRole("button", {
      name: /download synthetic data for reports/i,
    });

    await expect(btn).toHaveAttribute(
      "aria-label",
      "Download synthetic data for reports",
    );
  });

  test("AC5: Download button is keyboard focusable and activatable", async ({
    page,
  }) => {
    await mockBaseRoutes(page, [completeJob]);

    // Mock download endpoint so keyboard activation succeeds
    await page.route("/api/v1/jobs/7/download", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/octet-stream",
        headers: {
          "Content-Disposition": 'attachment; filename="reports_synth.parquet"',
        },
        body: Buffer.from("data"),
      }),
    );

    await page.goto("/dashboard");
    await page.waitForSelector("text=reports");

    const btn = page.getByRole("button", {
      name: /download synthetic data for reports/i,
    });

    // Focus and activate with keyboard
    await btn.focus();
    await expect(btn).toBeFocused();

    // Intercept the download request triggered by keyboard Enter
    const [downloadRequest] = await Promise.all([
      page.waitForRequest((req) => req.url().includes("/api/v1/jobs/7/download")),
      page.keyboard.press("Enter"),
    ]);

    expect(downloadRequest.url()).toContain("/api/v1/jobs/7/download");
  });
});
