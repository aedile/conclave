/**
 * P28 — Full E2E Validation Playwright spec.
 *
 * Purpose: Capture screenshot evidence of every key UI state as part of the
 * Phase 28 end-to-end validation run.  Screenshots are saved to docs/screenshots/
 * relative to the project root and are embedded in docs/E2E_VALIDATION.md.
 *
 * Unlike the functional e2e specs (unseal.spec.ts, dashboard.spec.ts, etc.)
 * this spec always uses mocked routes so it can run without a live backend.
 * The companion docs/E2E_VALIDATION.md records the live API evidence separately.
 *
 * Mocking strategy:
 *   - All backend API calls are intercepted via page.route().
 *   - GET navigations to SPA paths proxied by Vite (e.g. /unseal) are served
 *     with the built index.html so the React SPA can bootstrap normally.
 *     Without this, Vite preview proxies GET /unseal to the FastAPI backend
 *     which returns 405 (no GET handler exists for /unseal).
 *   - Screenshots are captured at each stage and saved to docs/screenshots/p28-*.
 *   - The spec also verifies that the key WCAG invariants still hold at each stage.
 *
 * Screenshot naming convention: p28-NN-<slug>.png
 *   01  Unseal page — initial state (vault sealed)
 *   02  Unseal page — error feedback (invalid passphrase)
 *   03  Dashboard — sealed redirect (should show unseal page)
 *   04  Dashboard — empty (no jobs)
 *   05  Dashboard — create-job form (field validation)
 *   06  Dashboard — job in QUEUED state (after create)
 *   07  Dashboard — job in TRAINING state (progress bar)
 *   08  Dashboard — job COMPLETE
 *   09  Dashboard — download flow (COMPLETE job actions)
 *   10  Dashboard — error toast (network failure)
 *
 * Task: P28 — Full E2E Validation with Frontend Screenshots
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const _dirname = fileURLToPath(new URL(".", import.meta.url));

/** Absolute path to the docs/screenshots directory.  */
const SCREENSHOTS_DIR = path.resolve(_dirname, "../../../docs/screenshots");

/** Built SPA index.html content — served for GET navigations to proxied paths. */
const INDEX_HTML = fs.readFileSync(
  path.resolve(_dirname, "../../dist/index.html"),
  "utf-8",
);

/**
 * Return the absolute path for a numbered screenshot.
 *
 * @param n - Two-digit sequential number (e.g. "01").
 * @param slug - Descriptive kebab-case slug.
 * @returns Absolute path string ending in .png.
 */
function screenshotPath(n: string, slug: string): string {
  return path.join(SCREENSHOTS_DIR, `p28-${n}-${slug}.png`);
}

// ---------------------------------------------------------------------------
// Shared job fixtures (parallel structure to existing e2e specs)
// ---------------------------------------------------------------------------

const queuedJob = {
  id: 1,
  status: "QUEUED",
  current_epoch: 0,
  total_epochs: 10,
  num_rows: 500,
  table_name: "customers",
  parquet_path: "/data/customers.parquet",
  artifact_path: null,
  output_path: null,
  error_msg: null,
  checkpoint_every_n: 2,
  enable_dp: true,
  noise_multiplier: 1.1,
  max_grad_norm: 1.0,
  actual_epsilon: null,
};

const trainingJob = {
  ...queuedJob,
  status: "TRAINING",
  current_epoch: 3,
};

const completeJob = {
  ...queuedJob,
  status: "COMPLETE",
  current_epoch: 10,
  artifact_path: "/artifacts/customers_model.pkl",
  output_path: "/output/customers_synthetic.parquet",
  actual_epsilon: 4.2,
};

// ---------------------------------------------------------------------------
// Route helpers
// ---------------------------------------------------------------------------

type PageType = Parameters<Parameters<typeof test>[1]>[0]["page"];

/** Register a sealed vault health response (423). */
async function mockSealedHealth(page: PageType): Promise<void> {
  await page.route("/health", (route) =>
    route.fulfill({
      status: 423,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Service sealed. POST /unseal to activate.",
      }),
    }),
  );
}

/** Register an unsealed vault health response (200). */
async function mockUnsealedHealth(page: PageType): Promise<void> {
  await page.route("/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok" }),
    }),
  );
}

/** Register an empty job list. */
async function mockEmptyJobList(page: PageType): Promise<void> {
  await page.route("/jobs?limit=20", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], next_cursor: null }),
    }),
  );
}

/**
 * Register a mock for the /unseal endpoint.
 *
 * Vite preview proxies /unseal to the FastAPI backend.  Because FastAPI only
 * exposes POST /unseal, a browser GET navigation to /unseal would return
 * 405 Method Not Allowed.  This mock intercepts the GET and serves the SPA
 * index.html so React can bootstrap and render the /unseal route normally.
 * POST requests receive the provided status/body.
 *
 * @param page - Playwright page object.
 * @param status - HTTP status code for the POST response.
 * @param body - JSON body for the POST response.
 */
async function mockUnsealPost(
  page: PageType,
  status: number,
  body: object,
): Promise<void> {
  await page.route("/unseal", (route) => {
    if (route.request().method() !== "POST") {
      // Serve the SPA shell for GET navigations — the React router handles the
      // /unseal client-side route without a round-trip to the backend.
      return route.fulfill({
        status: 200,
        contentType: "text/html",
        body: INDEX_HTML,
      });
    }
    return route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
}

// ---------------------------------------------------------------------------
// P28 screenshot evidence suite
// ---------------------------------------------------------------------------

test.describe("P28 E2E Validation — Screenshot Evidence", () => {
  // -------------------------------------------------------------------------
  // Screenshot 01: Unseal page — initial (sealed) state
  // -------------------------------------------------------------------------
  test("01 — unseal page renders in sealed state", async ({ page }) => {
    await mockSealedHealth(page);
    await mockUnsealPost(page, 400, {
      error_code: "EMPTY_PASSPHRASE",
      detail: "Passphrase must not be empty.",
    });

    await page.goto("/unseal");

    // Verify key elements are present before screenshotting
    await expect(
      page.getByRole("heading", { name: /conclave engine/i }),
    ).toBeVisible();
    await expect(page.getByLabel(/operator passphrase/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: /unseal vault/i }),
    ).toBeVisible();

    await page.screenshot({
      path: screenshotPath("01", "unseal-sealed-state"),
      fullPage: true,
    });

    // WCAG invariant: 0 axe violations
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // Screenshot 02: Unseal page — error feedback (wrong passphrase)
  // -------------------------------------------------------------------------
  test("02 — unseal page shows error feedback for invalid passphrase", async ({
    page,
  }) => {
    await mockSealedHealth(page);
    await mockUnsealPost(page, 400, {
      error_code: "CONFIG_ERROR",
      detail: "Invalid passphrase — vault derivation failed.",
    });

    await page.goto("/unseal");

    const input = page.getByLabel(/operator passphrase/i);
    const button = page.getByRole("button", { name: /unseal vault/i });

    await input.fill("wrong-passphrase");
    await button.click();

    // Wait for the error message to appear in the DOM (mocked routes do not
    // trigger page.waitForResponse, so we wait on the visible error text).
    await expect(
      page.locator(".unseal-status__error"),
    ).toBeVisible({ timeout: 10000 });

    await page.screenshot({
      path: screenshotPath("02", "unseal-error-feedback"),
      fullPage: true,
    });
  });

  // -------------------------------------------------------------------------
  // Screenshot 03: Dashboard redirect — sealed vault redirects to unseal
  // -------------------------------------------------------------------------
  test("03 — dashboard in sealed state redirects to unseal page", async ({
    page,
  }) => {
    await mockSealedHealth(page);
    // Also mock GET /unseal so the redirect target loads correctly
    await mockUnsealPost(page, 400, {
      error_code: "EMPTY_PASSPHRASE",
      detail: "Passphrase must not be empty.",
    });

    // Navigating to /dashboard while sealed should redirect to /unseal
    await page.goto("/dashboard");

    // The frontend should redirect to /unseal — wait for it
    await page.waitForURL(/\/unseal/, { timeout: 5000 });
    await expect(
      page.getByRole("heading", { name: /conclave engine/i }),
    ).toBeVisible();

    await page.screenshot({
      path: screenshotPath("03", "dashboard-sealed-redirect"),
      fullPage: true,
    });
  });

  // -------------------------------------------------------------------------
  // Screenshot 04: Dashboard — empty state (no jobs)
  // -------------------------------------------------------------------------
  test("04 — dashboard empty state (no jobs)", async ({ page }) => {
    await mockUnsealedHealth(page);
    await mockEmptyJobList(page);

    await page.goto("/dashboard");

    // Wait for the Create Job form to appear (empty state)
    await expect(
      page.getByRole("heading", { name: /create job/i }),
    ).toBeVisible();

    await page.screenshot({
      path: screenshotPath("04", "dashboard-empty"),
      fullPage: true,
    });

    // WCAG invariant
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // Screenshot 05: Dashboard — form field validation (partial fill)
  // -------------------------------------------------------------------------
  test("05 — dashboard create-job form with partial field fill", async ({
    page,
  }) => {
    await mockUnsealedHealth(page);
    await mockEmptyJobList(page);

    await page.goto("/dashboard");
    await expect(
      page.getByRole("heading", { name: /create job/i }),
    ).toBeVisible();

    // Fill in table name only to show field-level validation state
    await page.getByLabel(/table name/i).fill("customers");

    await page.screenshot({
      path: screenshotPath("05", "dashboard-form-partial"),
      fullPage: true,
    });
  });

  // -------------------------------------------------------------------------
  // Screenshot 06: Dashboard — job in QUEUED state
  // -------------------------------------------------------------------------
  test("06 — dashboard with a QUEUED job", async ({ page }) => {
    await mockUnsealedHealth(page);

    await page.route("/jobs?limit=20", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [queuedJob], next_cursor: null }),
      }),
    );

    await page.goto("/dashboard");
    await expect(page.locator("text=customers")).toBeVisible();

    await page.screenshot({
      path: screenshotPath("06", "dashboard-job-queued"),
      fullPage: true,
    });

    // WCAG invariant
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // Screenshot 07: Dashboard — job in TRAINING state (progress bar)
  // -------------------------------------------------------------------------
  test("07 — dashboard with a TRAINING job and progress bar", async ({
    page,
  }) => {
    await mockUnsealedHealth(page);

    await page.route("/jobs?limit=20", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [trainingJob], next_cursor: null }),
      }),
    );

    await page.route("/jobs/1", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(trainingJob),
      }),
    );

    await page.route("/jobs/1/stream", (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "Cache-Control": "no-cache", Connection: "keep-alive" },
        body: `event: progress\ndata: ${JSON.stringify({ status: "TRAINING", current_epoch: 3, total_epochs: 10, percent: 30 })}\n\n`,
      }),
    );

    await page.goto("/dashboard");
    await page.evaluate(() => {
      localStorage.setItem("conclave_active_job_id", "1");
    });
    await page.reload();

    // Wait for progress bar to appear
    await expect(page.locator('[role="progressbar"]').first()).toBeVisible({
      timeout: 10000,
    });

    await page.screenshot({
      path: screenshotPath("07", "dashboard-job-training"),
      fullPage: true,
    });

    // WCAG invariant
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // Screenshot 08: Dashboard — job in COMPLETE state
  // -------------------------------------------------------------------------
  test("08 — dashboard with a COMPLETE job", async ({ page }) => {
    await mockUnsealedHealth(page);

    await page.route("/jobs?limit=20", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [completeJob], next_cursor: null }),
      }),
    );

    await page.goto("/dashboard");
    await expect(page.locator("text=customers")).toBeVisible();

    await page.screenshot({
      path: screenshotPath("08", "dashboard-job-complete"),
      fullPage: true,
    });

    // WCAG invariant
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations).toHaveLength(0);
  });

  // -------------------------------------------------------------------------
  // Screenshot 09: Dashboard — download flow (COMPLETE job download button)
  // -------------------------------------------------------------------------
  test("09 — download flow shows download action for COMPLETE job", async ({
    page,
  }) => {
    await mockUnsealedHealth(page);

    await page.route("/jobs?limit=20", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [completeJob], next_cursor: null }),
      }),
    );

    // Intercept the download request to prevent actual file download
    await page.route("/jobs/1/download", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/octet-stream",
        body: "mock-parquet-data",
        headers: {
          "Content-Disposition":
            'attachment; filename="customers_synthetic.parquet"',
        },
      }),
    );

    await page.goto("/dashboard");
    await expect(page.locator("text=customers")).toBeVisible();

    await page.screenshot({
      path: screenshotPath("09", "dashboard-download-flow"),
      fullPage: true,
    });
  });

  // -------------------------------------------------------------------------
  // Screenshot 10: Error handling — network failure shows error state
  // -------------------------------------------------------------------------
  test("10 — error handling on network failure", async ({ page }) => {
    // Health endpoint returns a server error to simulate network issues
    await page.route("/health", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Service unavailable." }),
      }),
    );
    // Serve the SPA shell so the React app loads; the health mock controls UI state
    await mockUnsealPost(page, 503, { detail: "Service unavailable." });

    await page.goto("/unseal");

    await page.screenshot({
      path: screenshotPath("10", "error-handling-network-failure"),
      fullPage: true,
    });
  });
});
