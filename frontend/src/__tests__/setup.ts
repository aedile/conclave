/**
 * Vitest test setup — runs before each test file.
 *
 * Imports @testing-library/jest-dom to extend Vitest's expect with DOM
 * matchers (toBeInTheDocument, toHaveAttribute, etc.).
 *
 * Also provides a localStorage stub for jsdom environments where the
 * global `localStorage` may not be fully available.
 *
 * URL.createObjectURL / revokeObjectURL stub: jsdom does not implement
 * the Blob URL API. These stubs allow tests that exercise the download
 * anchor-click pattern (P23-T23.3) without throwing.
 */
import "@testing-library/jest-dom";

// ---------------------------------------------------------------------------
// localStorage stub — provides a consistent, in-memory implementation
// for all tests. The jsdom environment has localStorage but it is not
// always reliably exposed as a global in the vitest runner process.
// ---------------------------------------------------------------------------
const localStorageStore: Record<string, string> = {};

const localStorageMock: Storage = {
  getItem: (key: string): string | null => localStorageStore[key] ?? null,
  setItem: (key: string, value: string): void => {
    localStorageStore[key] = value;
  },
  removeItem: (key: string): void => {
    delete localStorageStore[key];
  },
  clear: (): void => {
    Object.keys(localStorageStore).forEach((k) => delete localStorageStore[k]);
  },
  key: (index: number): string | null =>
    Object.keys(localStorageStore)[index] ?? null,
  get length(): number {
    return Object.keys(localStorageStore).length;
  },
};

// Expose on both `window` and global so components and tests share the same object
Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
  configurable: true,
});

// ---------------------------------------------------------------------------
// URL Blob API stubs (jsdom does not implement createObjectURL/revokeObjectURL)
//
// P23-T23.3: The download handler in Dashboard creates an object URL from the
// response Blob and then revokes it. These stubs prevent "not a function"
// errors in the jsdom test environment. The stubs return a deterministic
// fake URL so tests can assert the anchor's href if needed.
// ---------------------------------------------------------------------------
Object.defineProperty(globalThis.URL, "createObjectURL", {
  value: (_blob: Blob): string => "blob:http://localhost/fake-object-url",
  writable: true,
  configurable: true,
});

Object.defineProperty(globalThis.URL, "revokeObjectURL", {
  value: (_url: string): void => {
    // no-op — nothing to revoke in jsdom
  },
  writable: true,
  configurable: true,
});
