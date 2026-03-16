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
 *
 * P20-T20.3 AC3: No inline style= attributes — all layout via CSS classes.
 * The input border state (error/normal) is handled by the
 * .unseal-form__input--error modifier class.
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
      tabIndex={-1}
      className="unseal-main"
    >
      <section
        aria-labelledby="unseal-heading"
        className="unseal-card"
      >
        <h1
          id="unseal-heading"
          className="unseal-card__title"
        >
          Conclave Engine
        </h1>
        <p className="unseal-card__subtitle">
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
              className="unseal-status__error"
            >
              {error.message}
            </div>
          )}

          {successMessage && (
            <div className="unseal-status__success">
              {successMessage}
            </div>
          )}
        </div>

        <form onSubmit={(e) => void handleSubmit(e)} noValidate>
          <div className="unseal-form__field">
            <label
              htmlFor="passphrase"
              className="unseal-form__label"
            >
              Operator Passphrase{" "}
              {/* Visible required indicator — aria-hidden so SR reads aria-required */}
              <span
                aria-hidden="true"
                className="unseal-form__required-indicator"
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
              className={`unseal-form__input${error ? " unseal-form__input--error" : ""}`}
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            aria-disabled={isLoading}
            className={`unseal-form__submit${isLoading ? " unseal-form__submit--loading" : ""}`}
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
                  className="unseal-form__spinner"
                />
                <span>Unsealing…</span>
              </>
            ) : (
              "Unseal Vault"
            )}
          </button>
        </form>
      </section>
    </main>
  );
}
