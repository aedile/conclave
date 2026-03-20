/**
 * Playwright end-to-end tests for the Unseal screen.
 *
 * Acceptance criteria:
 *   - @axe-core/playwright accessibility scan reports 0 violations
 *   - Network intercept: no requests attempt to load external resources
 *
 * These tests require the dev server to be running:
 *   cd frontend && npm run dev
 * Or run against the built app:
 *   cd frontend && npm run build && npm run preview
 *
 * CI: The frontend job in ci.yml runs these tests against the built app.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.describe("Unseal screen", () => {
  test.beforeEach(async ({ page }) => {
    // Intercept the /health and /unseal calls to serve controlled responses
    // so the test works without a real backend.
    //
    // Guard: only intercept fetch/XHR requests for /health — the document
    // navigation (GET /) must pass through so the SPA HTML loads correctly.
    await page.route("/health", (route) => {
      if (route.request().resourceType() === "document") {
        return route.continue();
      }
      return route.fulfill({
        status: 423,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Service sealed. POST /unseal to activate." }),
      });
    });

    // Guard: only intercept POST requests for /unseal so the SPA HTML
    // navigation (GET /) is not swallowed by this handler.
    await page.route("/unseal", (route) => {
      if (route.request().method() !== "POST") {
        return route.continue();
      }
      return route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          error_code: "EMPTY_PASSPHRASE",
          detail: "Passphrase must not be empty.",
        }),
      });
    });

    // Navigate to the root; React Router redirects to /unseal automatically.
    // We avoid navigating directly to /unseal because that path is a POST-only
    // API endpoint on the backend — a GET would receive a 405 response instead
    // of the SPA HTML.
    await page.goto("/");
    await page.waitForURL("**/unseal");
  });

  test("accessibility: 0 axe violations on Unseal screen", async ({ page }) => {
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    expect(results.violations).toHaveLength(0);
  });

  test("form renders with correct accessible elements", async ({ page }) => {
    await expect(page.getByRole("heading", { name: /conclave engine/i })).toBeVisible();
    await expect(page.getByLabel(/operator passphrase/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /unseal vault/i })).toBeVisible();
  });

  test("no requests attempt to load external resources", async ({ page }) => {
    const externalRequests: string[] = [];

    // Collect any requests that go outside localhost
    page.on("request", (request) => {
      const url = request.url();
      if (
        !url.startsWith("http://localhost") &&
        !url.startsWith("https://localhost") &&
        !url.startsWith("http://127.0.0.1") &&
        !url.startsWith("about:") &&
        !url.startsWith("data:")
      ) {
        externalRequests.push(url);
      }
    });

    // Reload the page and wait for network idle
    await page.reload({ waitUntil: "networkidle" });

    expect(externalRequests).toHaveLength(0);
  });

  test("password input type prevents passphrase visibility", async ({ page }) => {
    const input = page.getByLabel(/operator passphrase/i);
    await expect(input).toHaveAttribute("type", "password");
  });

  test("submit button is disabled during form submission", async ({ page }) => {
    // Override the unseal route to never resolve (simulates slow PBKDF2).
    // Guard: only intercept POST requests so non-POST requests pass through.
    await page.route("/unseal", (route) => {
      if (route.request().method() !== "POST") {
        return route.continue();
      }
      // Intentionally do not call route.fulfill — request hangs
    });

    const input = page.getByLabel(/operator passphrase/i);
    const button = page.getByRole("button", { name: /unseal vault/i });

    await input.fill("my-test-passphrase");
    await button.click();

    // After click, the button label changes from "Unseal Vault" to "Unsealing…"
    // while the request is in flight. Verify the loading-state button is
    // disabled and visible. The aria-live region also shows "Unsealing…" but
    // we target the button role directly to avoid strict-mode ambiguity.
    const loadingButton = page.getByRole("button", { name: /unsealing/i });
    await expect(loadingButton).toBeDisabled();
    await expect(loadingButton).toBeVisible();
  });
});
