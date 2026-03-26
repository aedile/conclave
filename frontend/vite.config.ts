import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    proxy: {
      // Proxy /api/v1/* to the FastAPI backend during development.
      // The rewrite is intentionally ABSENT — /api/v1/jobs must reach the
      // backend as /api/v1/jobs (T59.1: versioned paths pass through as-is).
      // In production, a reverse proxy (nginx) handles this routing.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // Direct unseal/health/license calls to the backend (infrastructure paths)
      "/unseal": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
      "/license": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    // Target modern browsers — no legacy polyfills needed for air-gapped deployment
    target: "es2020",
    // ADV-057: Disable source maps in production to prevent internal source disclosure.
    // Development builds retain source maps for debugging convenience.
    sourcemap: mode !== "production",
  },
}));
