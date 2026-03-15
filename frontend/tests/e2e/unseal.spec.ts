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
    await page.route("/health", (route) =>
      route.fulfill({
        status: 423,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Service sealed. POST /unseal to activate." }),
      }),
    );

    await page.route("/unseal", (route) =>
      route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          error_code: "EMPTY_PASSPHRASE",
          detail: "Passphrase must not be empty.",
        }),
      }),
    );

    await page.goto("/unseal");
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
    // Override the unseal route to never resolve (simulates slow PBKDF2)
    await page.route("/unseal", () => {
      // Intentionally do not call route.fulfill — request hangs
    });

    const input = page.getByLabel(/operator passphrase/i);
    const button = page.getByRole("button", { name: /unseal vault/i });

    await input.fill("my-test-passphrase");
    await button.click();

    // Button should be disabled immediately after click
    await expect(button).toBeDisabled();
    await expect(page.getByText(/unsealing/i)).toBeVisible();
  });
});
