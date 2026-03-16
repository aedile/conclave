import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    proxy: {
      // Proxy /api/* to the FastAPI backend during development.
      // In production, a reverse proxy (nginx) handles this routing.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      // Direct unseal/health/license calls to the backend
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
