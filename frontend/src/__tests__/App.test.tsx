/**
 * Vitest unit tests for App router guard behaviour.
 *
 * Tests that the RouterGuard redirects to /unseal on 423 or network error,
 * and allows /dashboard access when health returns 200.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is hoisted above imports — module-level mock
vi.mock("../api/client", () => ({
  getHealth: vi.fn(),
  postUnseal: vi.fn(),
}));

// Mock useNavigate for App component's RouterGuard
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
import App from "../App";

const mockGetHealth = vi.mocked(client.getHealth);

function renderApp(initialPath = "/dashboard") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App router guard", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockGetHealth.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the Unseal screen at /unseal without health check", () => {
    renderApp("/unseal");
    expect(
      screen.getByRole("heading", { name: /conclave engine/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(/operator passphrase/i),
    ).toBeInTheDocument();
  });

  it("redirects to /unseal when health returns 423 locked", async () => {
    mockGetHealth.mockResolvedValue({ status: "locked" });

    renderApp("/dashboard");

    // RouterGuard calls navigate — wait for the async health check to resolve
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/unseal", { replace: true });
    });
  });

  it("redirects to /unseal when health returns null (network error)", async () => {
    mockGetHealth.mockResolvedValue(null);

    renderApp("/dashboard");

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/unseal", { replace: true });
    });
  });

  it("does not redirect to /unseal when health returns 200 ok", async () => {
    mockGetHealth.mockResolvedValue({ status: "ok" });

    renderApp("/dashboard");

    // Wait for the health check to complete
    await waitFor(() => {
      // navigate should have been called with navigate('/unseal') if sealed
      // Check it was NOT called with /unseal
      const calls = mockNavigate.mock.calls;
      const unsealsCallCount = calls.filter(
        (c) => c[0] === "/unseal",
      ).length;
      expect(unsealsCallCount).toBe(0);
    });
  });

  it("redirects root path to /unseal via React Router Navigate", () => {
    renderApp("/");
    expect(
      screen.getByRole("heading", { name: /conclave engine/i }),
    ).toBeInTheDocument();
  });

  it("redirects unknown paths to /unseal via React Router Navigate", () => {
    renderApp("/nonexistent-path");
    expect(
      screen.getByRole("heading", { name: /conclave engine/i }),
    ).toBeInTheDocument();
  });
});
