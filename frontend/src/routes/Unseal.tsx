/**
 * Unseal — the vault unseal screen.
 *
 * WCAG 2.1 AA compliance:
 *   - Form label properly associated with input via htmlFor/id
 *   - Required field indicated with visible asterisk (aria-hidden) + aria-required
 *   - aria-describedby links the input to its error message region
 *   - aria-live="polite" role="status" announces status changes to screen readers
 *   - Loading state disables submit and shows visible indicator (ADV-019)
 *   - Spinner is aria-hidden; adjacent text communicates loading state
 *   - Auto-focus on mount for keyboard users
 *   - Minimum 4.5:1 contrast ratio enforced via CSS custom properties
 *   - Visible focus ring on all interactive elements (:focus-visible)
 *   - prefers-reduced-motion respected via global.css
 *
 * Error differentiation (ADV-018):
 *   - Network error: cannot reach server
 *   - EMPTY_PASSPHRASE: passphrase field was blank
 *   - ALREADY_UNSEALED: vault is already open
 *   - CONFIG_ERROR: server-side configuration problem → contact admin
 */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { postUnseal } from "../api/client";

type ErrorState = {
  type: "network" | "invalid_passphrase" | "config_error" | "already_unsealed";
  message: string;
};

/** Human-readable messages for each error code. */
const ERROR_MESSAGES: Record<string, string> = {
  network:
    "Unable to connect to the server. Please check your network connection.",
  invalid_passphrase: "Invalid passphrase. Please try again.",
  config_error:
    "Server configuration error. Please contact your administrator.",
  already_unsealed: "Vault is already unsealed. Redirecting…",
};

/**
 * Unseal form component.
 *
 * Renders a passphrase input form that calls POST /unseal and redirects to
 * the dashboard on success. Differentiates between network failure,
 * passphrase error, and server configuration errors.
 */
export default function Unseal() {
  const [passphrase, setPassphrase] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<ErrorState | null>(null);
  const [successMessage, setSuccessMessage] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  // Store timer IDs so they can be cleared on unmount (QA finding — no leak)
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  // Auto-focus the passphrase input on mount for keyboard users
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Set page title on mount for screen readers and browser history
  useEffect(() => {
    document.title = "Unseal Vault — Conclave Engine";
  }, []);

  // Clear any pending timers on unmount to prevent state updates on
  // an unmounted component (QA finding — setTimeout never cleaned up)
  useEffect(() => {
    return () => {
      if (timerRef.current !== undefined) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setSuccessMessage("");
    setIsLoading(true);

    const result = await postUnseal(passphrase);

    setIsLoading(false);

    if (result.ok) {
      setSuccessMessage("Vault unsealed. Redirecting to dashboard…");
      // Brief delay so screen readers announce the success message
      timerRef.current = setTimeout(() => void navigate("/dashboard"), 800);
      return;
    }

    if (result.error === null) {
      setError({ type: "network", message: ERROR_MESSAGES.network });
      return;
    }

    if (result.error) {
      switch (result.error.error_code) {
        case "EMPTY_PASSPHRASE":
          setError({
            type: "invalid_passphrase",
            message: ERROR_MESSAGES.invalid_passphrase,
          });
          break;
        case "ALREADY_UNSEALED":
          setError({
            type: "already_unsealed",
            message: ERROR_MESSAGES.already_unsealed,
          });
          timerRef.current = setTimeout(
            () => void navigate("/dashboard"),
            1200,
          );
          break;
        case "CONFIG_ERROR":
        default:
          setError({
            type: "config_error",
            message: ERROR_MESSAGES.config_error,
          });
          break;
      }
    }
  };

  return (
    <main
      id="main-content"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        padding: "var(--spacing-lg)",
        backgroundColor: "var(--color-bg)",
      }}
    >
      <section
        aria-labelledby="unseal-heading"
        style={{
          width: "100%",
          maxWidth: "420px",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-md)",
          padding: "var(--spacing-xl)",
        }}
      >
        <h1
          id="unseal-heading"
          style={{
            fontSize: "1.5rem",
            fontWeight: 700,
            color: "var(--color-text-primary)",
            marginBottom: "var(--spacing-xs)",
          }}
        >
          Conclave Engine
        </h1>
        <p
          style={{
            color: "var(--color-text-secondary)",
            marginBottom: "var(--spacing-xl)",
            fontSize: "0.875rem",
          }}
        >
          Enter the operator passphrase to unseal the vault.
        </p>

        {/*
         * aria-live="polite" region — announces status changes to screen readers
         * without interrupting ongoing speech. Wraps both error and success msgs.
         * The inner error div does NOT carry role="alert" — that would create a
         * conflicting live-region nest (UI/UX finding — conflicting live-region nesting).
         */}
        <div
          aria-live="polite"
          aria-atomic="true"
          role="status"
          id="unseal-status"
        >
          {error && (
            <div
              id="unseal-error"
              style={{
                backgroundColor: "rgba(248, 113, 113, 0.12)",
                border: "1px solid var(--color-error)",
                borderRadius: "var(--radius-sm)",
                padding: "var(--spacing-sm) var(--spacing-md)",
                color: "var(--color-error)",
                fontSize: "0.875rem",
                marginBottom: "var(--spacing-md)",
              }}
            >
              {error.message}
            </div>
          )}

          {successMessage && (
            <div
              style={{
                backgroundColor: "rgba(52, 211, 153, 0.12)",
                border: "1px solid var(--color-success)",
                borderRadius: "var(--radius-sm)",
                padding: "var(--spacing-sm) var(--spacing-md)",
                color: "var(--color-success)",
                fontSize: "0.875rem",
                marginBottom: "var(--spacing-md)",
              }}
            >
              {successMessage}
            </div>
          )}
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} noValidate>
          <div style={{ marginBottom: "var(--spacing-lg)" }}>
            <label
              htmlFor="passphrase"
              style={{
                display: "block",
                fontWeight: 600,
                fontSize: "0.875rem",
                color: "var(--color-text-primary)",
                marginBottom: "var(--spacing-xs)",
              }}
            >
              Operator Passphrase{" "}
              {/* Visible required indicator — aria-hidden so SR reads aria-required */}
              <span
                aria-hidden="true"
                style={{ color: "var(--color-error)" }}
              >
                *
              </span>
            </label>
            <input
              ref={inputRef}
              id="passphrase"
              type="password"
              name="passphrase"
              value={passphrase}
              onChange={(e) => setPassphrase(e.target.value)}
              disabled={isLoading}
              autoComplete="current-password"
              aria-describedby={error ? "unseal-error" : "unseal-status"}
              aria-invalid={error !== null}
              aria-required="true"
              placeholder="Enter passphrase"
              style={{
                width: "100%",
                padding: "var(--spacing-sm) var(--spacing-md)",
                backgroundColor: "var(--color-bg)",
                border: `1px solid ${error ? "var(--color-error)" : "var(--color-border)"}`,
                borderRadius: "var(--radius-sm)",
                color: "var(--color-text-primary)",
                fontSize: "1rem",
                transition: "border-color 0.15s ease",
              }}
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            aria-disabled={isLoading}
            style={{
              width: "100%",
              padding: "var(--spacing-sm) var(--spacing-md)",
              backgroundColor: isLoading
                ? "var(--color-border)"
                : "var(--color-accent)",
              color: "white",
              border: "none",
              borderRadius: "var(--radius-sm)",
              fontSize: "1rem",
              fontWeight: 600,
              cursor: isLoading ? "not-allowed" : "pointer",
              transition: "background-color 0.15s ease",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "var(--spacing-sm)",
              minHeight: "2.75rem",
            }}
          >
            {isLoading ? (
              <>
                {/*
                 * Spinner is decorative — aria-hidden so the adjacent
                 * "Unsealing…" text communicates state to screen readers
                 * (UI/UX finding — spinner double-announcement).
                 */}
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-block",
                    width: "1rem",
                    height: "1rem",
                    border: "2px solid rgba(255,255,255,0.3)",
                    borderTopColor: "white",
                    borderRadius: "50%",
                    animation: "spin 0.8s linear infinite",
                  }}
                />
                <span>Unsealing…</span>
              </>
            ) : (
              "Unseal Vault"
            )}
          </button>
        </form>

        {/* CSS keyframe for the loading spinner */}
        <style>{`
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
        `}</style>
      </section>
    </main>
  );
}
