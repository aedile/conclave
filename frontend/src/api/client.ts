/**
 * API client for the Conclave Engine backend.
 *
 * Typed fetch wrappers that translate HTTP responses into discriminated
 * union results. The caller never sees raw Response objects — every call
 * resolves to either a success payload or a structured ApiError.
 *
 * CONSTITUTION: No external API calls. All requests target the same origin
 * (proxied via Vite to http://localhost:8000 in development).
 */

/** Structured error returned by the /unseal endpoint on ValueError. */
export interface UnsealErrorResponse {
  error_code: "EMPTY_PASSPHRASE" | "ALREADY_UNSEALED" | "CONFIG_ERROR";
  detail: string;
}

/** Successful response from the /unseal endpoint. */
export interface UnsealSuccessResponse {
  status: "unsealed";
}

/** Discriminated union result type for unseal calls. */
export type UnsealResult =
  | { ok: true; data: UnsealSuccessResponse }
  | { ok: false; error: UnsealErrorResponse }
  | { ok: false; error: null; networkError: true };

/**
 * POST /unseal — derive the KEK and transition the vault to unsealed state.
 *
 * Differentiates between three failure modes:
 *   1. Network error (no response) → { networkError: true }
 *   2. Structured 400 from backend → { error: UnsealErrorResponse }
 *   3. Unexpected HTTP error → generic error with CONFIG_ERROR code
 *
 * @param passphrase - The operator passphrase to derive the KEK from.
 * @returns A discriminated union result that the caller should switch on.
 */
export async function postUnseal(passphrase: string): Promise<UnsealResult> {
  let response: Response;

  try {
    response = await fetch("/unseal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase }),
    });
  } catch {
    // fetch() throws on network failure (DNS, CORS, offline, etc.)
    return { ok: false, error: null, networkError: true };
  }

  if (response.ok) {
    const data = (await response.json()) as UnsealSuccessResponse;
    return { ok: true, data };
  }

  if (response.status === 400) {
    const error = (await response.json()) as UnsealErrorResponse;
    return { ok: false, error };
  }

  // Unexpected HTTP status — treat as configuration error
  return {
    ok: false,
    error: {
      error_code: "CONFIG_ERROR",
      detail: `Unexpected server response: HTTP ${response.status}`,
    },
  };
}

/** Response from the /health endpoint. */
export interface HealthResponse {
  status: "ok";
}

/**
 * GET /health — check if the backend is reachable.
 *
 * Returns null on network error so callers can gracefully degrade.
 *
 * @returns The health response or null on network failure.
 */
export async function getHealth(): Promise<
  HealthResponse | { status: "locked" } | null
> {
  let response: Response;

  try {
    response = await fetch("/health");
  } catch {
    return null;
  }

  if (response.status === 423) {
    return { status: "locked" };
  }

  if (response.ok) {
    return (await response.json()) as HealthResponse;
  }

  return null;
}
