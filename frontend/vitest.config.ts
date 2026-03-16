// @ts-nocheck — vitest/config and the outer vite package have aligned but
// technically distinct Plugin types; @ts-nocheck avoids the false positive.
// The runtime behaviour is correct — vitest uses its own bundled vite.
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    // Only run unit tests — exclude Playwright e2e tests
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules/**", "dist/**", "tests/**"],
    setupFiles: ["./src/__tests__/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "html"],
      // Exclude entry point, test infrastructure, and non-source files from
      // coverage. main.tsx is a framework entry point (creates DOM root, wraps
      // with BrowserRouter + StrictMode) — no business logic to test.
      // eslint.config.js is a tooling config file; vite-env.d.ts is a
      // TypeScript declaration file — neither contains executable app logic.
      exclude: [
        "node_modules/**",
        "dist/**",
        "**/__tests__/**",
        "**/tests/**",
        "vite.config.ts",
        "vitest.config.ts",
        "playwright.config.ts",
        "eslint.config.js",
        "src/vite-env.d.ts",
        // Entry point — tested indirectly via component tests
        "src/main.tsx",
      ],
      // 90% threshold matches project quality gate
      thresholds: {
        lines: 90,
        functions: 90,
        branches: 90,
        statements: 90,
      },
    },
  },
});
