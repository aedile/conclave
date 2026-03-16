/**
 * App — root React component with router guard.
 *
 * Routes:
 *   /unseal    → Unseal vault screen (always accessible)
 *   /dashboard → Main dashboard (requires unsealed vault)
 *   /          → Redirect to /unseal (router guard checks health first)
 *   *          → Redirect to /unseal
 *
 * Router guard logic:
 *   1. GET /health on mount
 *   2. If response is 423 Locked → redirect to /unseal
 *   3. If response is 200 OK → allow /dashboard access
 *   4. If network error → redirect to /unseal (safe default)
 *
 * ErrorBoundary wraps the Routes tree so any unhandled React render error
 * is caught and displayed as an RFC 7807 remediation card.
 *
 * WCAG 2.1 AA 2.4.1: A skip-to-content link is rendered as the very first
 * element so keyboard users can bypass repeated navigation blocks.
 */

import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { getHealth } from "./api/client";
import ErrorBoundary from "./components/ErrorBoundary";
import Dashboard from "./routes/Dashboard";
import Unseal from "./routes/Unseal";

type AppState = "loading" | "sealed" | "unsealed";

/**
 * RouterGuard — checks vault state on mount and redirects appropriately.
 *
 * Wraps the protected /dashboard route. If the vault is sealed, the user
 * is redirected to /unseal.
 */
function RouterGuard({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AppState>("loading");
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    getHealth()
      .then((result) => {
        if (cancelled) return;
        if (result === null || result.status === "locked") {
          setState("sealed");
          void navigate("/unseal", { replace: true });
        } else {
          setState("unsealed");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState("sealed");
          void navigate("/unseal", { replace: true });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [navigate]);

  if (state === "loading") {
    return (
      <div
        role="status"
        aria-label="Loading"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          color: "var(--color-text-secondary)",
          fontFamily: "var(--font-family)",
        }}
      >
        Initialising…
      </div>
    );
  }

  if (state === "sealed") {
    return null;
  }

  return <>{children}</>;
}

/**
 * App — root application component.
 *
 * Uses React Router v6 declarative routing with the RouterGuard wrapper
 * protecting the /dashboard route. The ErrorBoundary wraps all routes
 * so any unhandled render error is caught gracefully.
 *
 * The skip-to-content link is the first rendered element so that keyboard
 * focus order reaches it before any navigation or content blocks.
 */
export default function App() {
  return (
    <>
      {/* WCAG 2.1 AA 2.4.1 — Skip navigation link.
          Visually hidden; becomes visible on keyboard focus.
          Allows users to bypass repeated page structure and jump
          directly to the main content region (id="main-content"). */}
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <ErrorBoundary>
        <Routes>
          <Route path="/unseal" element={<Unseal />} />
          <Route
            path="/dashboard"
            element={
              <RouterGuard>
                <Dashboard />
              </RouterGuard>
            }
          />
          {/* Default: redirect root and unknown paths to /unseal */}
          <Route path="/" element={<Navigate to="/unseal" replace />} />
          <Route path="*" element={<Navigate to="/unseal" replace />} />
        </Routes>
      </ErrorBoundary>
    </>
  );
}
