/**
 * Vitest test setup — runs before each test file.
 *
 * Imports @testing-library/jest-dom to extend Vitest's expect with DOM
 * matchers (toBeInTheDocument, toHaveAttribute, etc.).
 *
 * Also provides a localStorage stub for jsdom environments where the
 * global `localStorage` may not be fully available.
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
