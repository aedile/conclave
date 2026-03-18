/**
 * Vitest unit tests for the Unseal component.
 *
 * Tests rendering, loading state, error differentiation, and
 * redirect-on-success behaviour.
 *
 * WCAG 2.1 AA: verifies that form elements have correct ARIA attributes.
 *
 * P27-T27.3 update: The submit button now uses AsyncButton, which renders
 * the loading text in two places — inside the button and in the associated
 * aria-live region (for screen reader announcement). The "disables the submit
 * button while loading" test was updated to use toHaveTextContent on the
 * button element directly, since getByText(/unsealing/i) would now match
 * multiple elements.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UnsealResult } from "../api/client";

// vi.mock is hoisted above imports — module-level mock for postUnseal
vi.mock("../api/client", () => ({
  postUnseal: vi.fn(),
  getHealth: vi.fn(),
}));

// Mock the navigate function from react-router-dom
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Import AFTER mock setup
import * as client from "../api/client";
import Unseal from "../routes/Unseal";

const mockPostUnseal = vi.mocked(client.postUnseal);

function renderUnseal() {
  return render(
    <MemoryRouter>
      <Unseal />
    </MemoryRouter>,
  );
}

describe("Unseal component", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockPostUnseal.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the form with a heading", () => {
    renderUnseal();
    expect(
      screen.getByRole("heading", { name: /conclave engine/i }),
    ).toBeInTheDocument();
  });

  it("renders a labelled password input", () => {
    renderUnseal();
    const input = screen.getByLabelText(/operator passphrase/i);
    expect(input).toBeInTheDocument();
    expect(input).toHaveAttribute("type", "password");
  });

  it("renders a submit button with correct text", () => {
    renderUnseal();
    expect(
      screen.getByRole("button", { name: /unseal vault/i }),
    ).toBeInTheDocument();
  });

  it("auto-focuses the passphrase input on mount", () => {
    renderUnseal();
    const input = screen.getByLabelText(/operator passphrase/i);
    expect(input).toHaveFocus();
  });

  it("disables the submit button while loading", async () => {
    // Never resolves so we can observe the loading state
    mockPostUnseal.mockImplementation(
      () => new Promise<UnsealResult>(() => {}),
    );

    const user = userEvent.setup();
    renderUnseal();

    const button = screen.getByRole("button", { name: /unseal vault/i });
    const input = screen.getByLabelText(/operator passphrase/i);

    await user.type(input, "my-passphrase");
    await user.click(button);

    // P27-T27.3: AsyncButton renders loadingText in both the button (visible)
    // and the aria-live region (for AT). Use toHaveTextContent on the button
    // element to assert the loading text without ambiguity.
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent(/unsealing/i);
  });

  it("shows a network error message on fetch failure", async () => {
    mockPostUnseal.mockResolvedValue({
      ok: false,
      error: null,
      networkError: true,
    });

    const user = userEvent.setup();
    renderUnseal();

    await user.type(screen.getByLabelText(/operator passphrase/i), "test");
    await user.click(screen.getByRole("button", { name: /unseal vault/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/unable to connect to the server/i),
      ).toBeInTheDocument();
    });
  });

  it("shows passphrase error on EMPTY_PASSPHRASE code", async () => {
    mockPostUnseal.mockResolvedValue({
      ok: false,
      error: { error_code: "EMPTY_PASSPHRASE", detail: "Passphrase must not be empty." },
    });

    const user = userEvent.setup();
    renderUnseal();

    await user.click(screen.getByRole("button", { name: /unseal vault/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/invalid passphrase\. please try again/i),
      ).toBeInTheDocument();
    });
  });

  it("shows config error message on CONFIG_ERROR code", async () => {
    mockPostUnseal.mockResolvedValue({
      ok: false,
      error: { error_code: "CONFIG_ERROR", detail: "VAULT_SEAL_SALT not set." },
    });

    const user = userEvent.setup();
    renderUnseal();

    await user.type(screen.getByLabelText(/operator passphrase/i), "passphrase");
    await user.click(screen.getByRole("button", { name: /unseal vault/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/server configuration error/i),
      ).toBeInTheDocument();
    });
  });

  it("shows already-unsealed message and redirects after delay on ALREADY_UNSEALED code", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    mockPostUnseal.mockResolvedValue({
      ok: false,
      error: {
        error_code: "ALREADY_UNSEALED",
        detail: "Vault is already unsealed.",
      },
    });

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderUnseal();

    await user.type(screen.getByLabelText(/operator passphrase/i), "passphrase");
    await user.click(screen.getByRole("button", { name: /unseal vault/i }));

    await waitFor(() => {
      expect(screen.getByText(/vault is already unsealed/i)).toBeInTheDocument();
    });

    // Advance past the 1200ms redirect delay
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });

    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");

    vi.useRealTimers();
  });

  it("redirects to dashboard on successful unseal", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    mockPostUnseal.mockResolvedValue({
      ok: true,
      data: { status: "unsealed" },
    });

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderUnseal();

    await user.type(
      screen.getByLabelText(/operator passphrase/i),
      "correct-passphrase",
    );
    await user.click(screen.getByRole("button", { name: /unseal vault/i }));

    await waitFor(() => {
      expect(screen.getByText(/vault unsealed/i)).toBeInTheDocument();
    });

    // Advance past the 800ms redirect delay
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");

    vi.useRealTimers();
  });

  it("button is re-enabled after a failed request", async () => {
    mockPostUnseal.mockResolvedValue({
      ok: false,
      error: null,
      networkError: true,
    });

    const user = userEvent.setup();
    renderUnseal();

    const button = screen.getByRole("button", { name: /unseal vault/i });
    await user.click(button);

    // After the mock resolves and state updates, button is re-enabled
    await waitFor(() => {
      // isLoading is set back to false after the request completes
      expect(screen.getByRole("button", { name: /unseal vault/i })).not.toBeDisabled();
    });
  });

  it("input has aria-invalid=true when error is present", async () => {
    mockPostUnseal.mockResolvedValue({
      ok: false,
      error: { error_code: "EMPTY_PASSPHRASE", detail: "Empty." },
    });

    const user = userEvent.setup();
    renderUnseal();

    await user.click(screen.getByRole("button", { name: /unseal vault/i }));

    await waitFor(() => {
      // Wait for the error state to be rendered, then check aria-invalid
      expect(screen.getByText(/invalid passphrase/i)).toBeInTheDocument();
    });

    const input = screen.getByLabelText(/operator passphrase/i);
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("sets document.title on mount", () => {
    renderUnseal();
    expect(document.title).toBe("Unseal Vault — Conclave Engine");
  });
});
