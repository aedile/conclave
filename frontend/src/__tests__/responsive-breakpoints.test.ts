/**
 * Vitest unit tests for responsive CSS breakpoints — P27-T27.1.
 *
 * Strategy: jsdom does not apply CSS from imported stylesheets, so these
 * tests read the raw stylesheet text and assert on its structure. This is
 * the only reliable way to verify CSS properties and media queries in a
 * unit-test environment without a real browser.
 *
 * Each AC from the task spec is tested explicitly.
 */

import { readFileSync } from "fs";
import { resolve } from "path";
import { describe, expect, it, beforeAll } from "vitest";

// ---------------------------------------------------------------------------
// Load the stylesheet once for all tests
// ---------------------------------------------------------------------------

let cssText: string;

beforeAll(() => {
  const cssPath = resolve(
    __dirname,
    "..",
    "styles",
    "global.css",
  );
  cssText = readFileSync(cssPath, "utf-8");
});

// ---------------------------------------------------------------------------
// AC1 — Breakpoint custom properties and @media queries defined
// ---------------------------------------------------------------------------

describe("AC1 — Breakpoint custom properties", () => {
  it("defines --breakpoint-mobile custom property at 640px", () => {
    expect(cssText).toMatch(/--breakpoint-mobile:\s*640px/);
  });

  it("defines --breakpoint-tablet custom property at 1024px", () => {
    expect(cssText).toMatch(/--breakpoint-tablet:\s*1024px/);
  });

  it("includes a max-width 640px media query for mobile", () => {
    expect(cssText).toMatch(/@media\s*\([^)]*max-width:\s*640px[^)]*\)/);
  });

  it("includes a max-width 1024px media query for tablet", () => {
    expect(cssText).toMatch(/@media\s*\([^)]*max-width:\s*1024px[^)]*\)/);
  });
});

// ---------------------------------------------------------------------------
// AC2 — Dashboard responsive layout
// ---------------------------------------------------------------------------

describe("AC2 — Dashboard responsive layout", () => {
  it("includes .dashboard-jobs__list responsive override in mobile media query", () => {
    // The mobile breakpoint block must contain a .dashboard-jobs__list rule
    const mobileBlock = extractMediaBlock(cssText, "640px");
    expect(mobileBlock).toMatch(/\.dashboard-jobs__list/);
  });

  it("includes full-width form inputs in mobile or tablet media query", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    const tabletBlock = extractMediaBlock(cssText, "1024px");
    const combined = mobileBlock + tabletBlock;
    // At least one breakpoint overrides the form input width
    expect(combined).toMatch(/\.dashboard-form__input/);
  });

  it("reduces dashboard padding on mobile", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    // padding shorthand or padding-specific property on .dashboard-main
    expect(mobileBlock).toMatch(/\.dashboard-main/);
  });
});

// ---------------------------------------------------------------------------
// AC3 — Unseal page responsive behaviour
// ---------------------------------------------------------------------------

describe("AC3 — Unseal card mobile responsiveness", () => {
  it("removes max-width constraint on unseal card at mobile breakpoint", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    // The mobile override must target .unseal-card and set max-width: none or 100%
    expect(mobileBlock).toMatch(/\.unseal-card/);
    expect(mobileBlock).toMatch(/max-width:\s*(none|100%)/);
  });

  it("ensures unseal form input meets 44px WCAG 2.5.5 touch target on mobile", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    // min-height of at least 44px (2.75rem = 44px at 16px root) on unseal input or submit
    expect(mobileBlock).toMatch(/\.unseal-form__input|\.unseal-form__submit/);
    expect(mobileBlock).toMatch(/min-height:\s*(44px|2\.75rem|3rem|48px)/);
  });

  it("ensures unseal form button meets 44px WCAG 2.5.5 touch target on mobile", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    expect(mobileBlock).toMatch(/\.unseal-form__submit/);
    expect(mobileBlock).toMatch(/min-height:\s*(44px|2\.75rem|3rem|48px)/);
  });
});

// ---------------------------------------------------------------------------
// AC4 — Fluid typography scaling with clamp()
// ---------------------------------------------------------------------------

describe("AC4 — Fluid typography with clamp()", () => {
  it("uses clamp() for font-size on html or body for fluid scaling", () => {
    // clamp() should appear in the stylesheet for base font-size
    expect(cssText).toMatch(/font-size:\s*clamp\(/);
  });
});

// ---------------------------------------------------------------------------
// AC5 — RFC7807Toast full-width on mobile
// ---------------------------------------------------------------------------

describe("AC5 — RFC7807Toast mobile full-width", () => {
  it("makes rfc7807-toast full-width on mobile", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    expect(mobileBlock).toMatch(/\.rfc7807-toast/);
  });

  it("removes left/right margin on rfc7807-toast at mobile breakpoint", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    // The toast should be repositioned to full-width with appropriate inset/margin
    expect(mobileBlock).toMatch(/\.rfc7807-toast/);
    // width should be 100% or calc(100% - padding)
    expect(mobileBlock).toMatch(/width:\s*(100%|calc\(100%)/);
  });
});

// ---------------------------------------------------------------------------
// AC7 — prefers-reduced-motion preserved
// ---------------------------------------------------------------------------

describe("AC7 — prefers-reduced-motion preserved", () => {
  it("retains the existing prefers-reduced-motion media query", () => {
    expect(cssText).toMatch(/prefers-reduced-motion/);
  });

  it("retains the job-card__progress-fill transition:none rule inside reduced-motion", () => {
    const reducedMotionBlock = extractMediaBlock(cssText, "prefers-reduced-motion");
    expect(reducedMotionBlock).toMatch(/\.job-card__progress-fill/);
    expect(reducedMotionBlock).toMatch(/transition:\s*none/);
  });
});

// ---------------------------------------------------------------------------
// Focus indicator preservation — Known Failure Pattern guard
// ---------------------------------------------------------------------------

describe("Focus indicator — preserved at all breakpoints", () => {
  it("retains :focus-visible outline rule in the base stylesheet", () => {
    expect(cssText).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px/s);
  });

  it("does not override :focus-visible outline to none inside any media query", () => {
    // Collect all media query blocks and ensure none set focus-visible outline to none
    const mediaBlocks = extractAllMediaBlocks(cssText);
    for (const block of mediaBlocks) {
      // skip the prefers-reduced-motion block (which is about motion, not focus)
      if (block.includes("prefers-reduced-motion")) continue;
      // No media query should suppress the focus-visible outline
      const suppressesFocus =
        /:focus-visible[^{]*\{[^}]*outline:\s*none/s.test(block);
      expect(suppressesFocus).toBe(false);
    }
  });
});

// ---------------------------------------------------------------------------
// BEM convention guard — Known Failure Pattern guard
// ---------------------------------------------------------------------------

describe("BEM convention — responsive overrides follow existing naming", () => {
  it("responsive block selectors use existing BEM class names without new modifiers", () => {
    const mobileBlock = extractMediaBlock(cssText, "640px");
    const tabletBlock = extractMediaBlock(cssText, "1024px");
    const combined = mobileBlock + tabletBlock;

    // All class selectors must be valid BEM names already used in the base CSS
    // We spot-check the known classes that should be overridden
    const expectedClasses = [
      ".dashboard-main",
      ".dashboard-form__input",
      ".unseal-card",
      ".rfc7807-toast",
    ];
    for (const cls of expectedClasses) {
      // Each expected class must appear in at least one breakpoint block
      expect(
        mobileBlock.includes(cls) || tabletBlock.includes(cls),
        `Expected class ${cls} to appear in at least one breakpoint media query`,
      ).toBe(true);
    }
  });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Extracts the content of a @media block whose condition string contains
 * the given substring. Returns all such blocks concatenated if multiple match.
 *
 * Handles nested braces correctly by counting depth.
 */
function extractMediaBlock(css: string, conditionSubstring: string): string {
  const results: string[] = [];
  let searchFrom = 0;

  while (true) {
    const mediaIndex = css.indexOf("@media", searchFrom);
    if (mediaIndex === -1) break;

    // Find the opening brace of this media block
    const openBrace = css.indexOf("{", mediaIndex);
    if (openBrace === -1) break;

    const condition = css.slice(mediaIndex, openBrace);

    // Walk forward to find the matching closing brace (handling nesting)
    let depth = 1;
    let pos = openBrace + 1;
    while (pos < css.length && depth > 0) {
      if (css[pos] === "{") depth++;
      else if (css[pos] === "}") depth--;
      pos++;
    }

    const blockContent = css.slice(openBrace + 1, pos - 1);

    if (condition.includes(conditionSubstring)) {
      results.push(blockContent);
    }

    searchFrom = pos;
  }

  return results.join("\n");
}

/**
 * Returns all @media block bodies as an array of strings.
 */
function extractAllMediaBlocks(css: string): string[] {
  const results: string[] = [];
  let searchFrom = 0;

  while (true) {
    const mediaIndex = css.indexOf("@media", searchFrom);
    if (mediaIndex === -1) break;

    const openBrace = css.indexOf("{", mediaIndex);
    if (openBrace === -1) break;

    let depth = 1;
    let pos = openBrace + 1;
    while (pos < css.length && depth > 0) {
      if (css[pos] === "{") depth++;
      else if (css[pos] === "}") depth--;
      pos++;
    }

    results.push(css.slice(openBrace + 1, pos - 1));
    searchFrom = pos;
  }

  return results;
}
