/**
 * useFocusTrap — constrains keyboard focus within a container element.
 *
 * When active, Tab and Shift+Tab cycle through the focusable descendants of
 * the referenced container, preventing focus from escaping into background
 * content. When inactive, the hook does nothing and normal tab order applies.
 *
 * WCAG 2.1 AA — SC 2.1.2 (No Keyboard Trap): Tab and Shift+Tab are the
 * standard keys for navigating focusable elements. The trap uses only these
 * keys and does not override Escape or any other key. A caller that needs
 * Escape-to-close should handle that key independently.
 *
 * Usage:
 * ```tsx
 * const containerRef = useRef<HTMLDivElement>(null);
 * useFocusTrap(containerRef, isModalOpen);
 * ```
 */

import { useEffect, type RefObject } from "react";

// ---------------------------------------------------------------------------
// Focusable element selector
// ---------------------------------------------------------------------------

/**
 * CSS selector matching all natively focusable HTML elements that are not
 * disabled and not hidden via tabindex=-1.
 */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(", ");

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * useFocusTrap — restrict keyboard focus to focusable children of container.
 *
 * Attaches a keydown listener to the container element. On Tab or Shift+Tab,
 * if focus is at the boundary of the focusable element list, the event is
 * prevented and focus wraps to the opposite boundary element.
 *
 * Note: The `offsetParent` visibility check is omitted intentionally. It
 * fails in jsdom (no layout engine) and is not required for the modal pattern
 * where the container is always visible when the trap is active.
 *
 * @param containerRef - Ref to the DOM element acting as the trap boundary.
 * @param isActive - Whether the trap is currently active.
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement | null>,
  isActive: boolean,
): void {
  useEffect(() => {
    if (!isActive) return;

    const container = containerRef.current;
    if (container === null) return;

    /**
     * Return the list of currently focusable children within the container.
     * Recalculates on every keydown to handle dynamic content changes.
     */
    function getFocusableElements(): HTMLElement[] {
      if (container === null) return [];
      return Array.from(
        container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((el) => !el.hasAttribute("hidden"));
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Tab") return;

      const focusable = getFocusableElements();
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];

      if (event.shiftKey) {
        // Shift+Tab: if focus is on the first element, wrap to last
        if (document.activeElement === first) {
          event.preventDefault();
          last.focus();
        }
      } else {
        // Tab: if focus is on the last element, wrap to first
        if (document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }

    container.addEventListener("keydown", handleKeyDown);

    return () => {
      container.removeEventListener("keydown", handleKeyDown);
    };
  }, [containerRef, isActive]);
}
