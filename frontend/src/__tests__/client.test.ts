/**
 * Vitest unit tests for the API client module.
 *
 * Tests postUnseal and getHealth discriminated union return values
 * using the global fetch mock.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getHealth, postUnseal } from "../api/client";

// Mock the global fetch
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

/** Create a mock Response object. */
function mockResponse(
  status: number,
  body: unknown,
  ok: boolean = status >= 200 && status < 300,
): Response {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
    headers: new Headers(),
  } as unknown as Response;
}

describe("postUnseal", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("returns { ok: true, data } on HTTP 200 response", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(200, { status: "unsealed" }),
    );

    const result = await postUnseal("correct-passphrase");

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.status).toBe("unsealed");
    }
  });

  it("returns { ok: false, error } with EMPTY_PASSPHRASE on 400", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(400, { error_code: "EMPTY_PASSPHRASE", detail: "Empty." }, false),
    );

    const result = await postUnseal("");

    expect(result.ok).toBe(false);
    if (!result.ok && result.error) {
      expect(result.error.error_code).toBe("EMPTY_PASSPHRASE");
      expect(result.error.detail).toBe("Empty.");
    }
  });

  it("returns { ok: false, error } with CONFIG_ERROR on 400", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(400, { error_code: "CONFIG_ERROR", detail: "Salt not set." }, false),
    );

    const result = await postUnseal("passphrase");

    expect(result.ok).toBe(false);
    if (!result.ok && result.error) {
      expect(result.error.error_code).toBe("CONFIG_ERROR");
    }
  });

  it("returns { ok: false, networkError: true } when fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await postUnseal("passphrase");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      // 'networkError' only exists on the null-error variant
      if (result.error === null) {
        expect(result.networkError).toBe(true);
      } else {
        expect.fail('Expected networkError variant');
      }
    }
  });

  it("returns CONFIG_ERROR for unexpected non-400 HTTP error status", async () => {
    mockFetch.mockResolvedValue(mockResponse(503, {}, false));

    const result = await postUnseal("passphrase");

    expect(result.ok).toBe(false);
    if (!result.ok && result.error) {
      expect(result.error.error_code).toBe("CONFIG_ERROR");
      expect(result.error.detail).toContain("503");
    }
  });

  it("sends passphrase in request body as JSON", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(200, { status: "unsealed" }),
    );

    await postUnseal("my-secret-passphrase");

    expect(mockFetch).toHaveBeenCalledWith("/unseal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passphrase: "my-secret-passphrase" }),
    });
  });
});

describe("getHealth", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("returns { status: 'ok' } on HTTP 200", async () => {
    mockFetch.mockResolvedValue(mockResponse(200, { status: "ok" }));

    const result = await getHealth();

    expect(result).toEqual({ status: "ok" });
  });

  it("returns { status: 'locked' } on HTTP 423", async () => {
    mockFetch.mockResolvedValue(
      mockResponse(423, { detail: "Service sealed." }, false),
    );

    const result = await getHealth();

    expect(result).toEqual({ status: "locked" });
  });

  it("returns null when fetch throws (network error)", async () => {
    mockFetch.mockRejectedValue(new Error("Network failure"));

    const result = await getHealth();

    expect(result).toBeNull();
  });

  it("returns null for unexpected non-200/423 status codes", async () => {
    mockFetch.mockResolvedValue(mockResponse(500, {}, false));

    const result = await getHealth();

    expect(result).toBeNull();
  });
});
