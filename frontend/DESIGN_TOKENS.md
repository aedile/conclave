# Design Tokens Reference

CSS custom property reference for the Conclave Engine UI (`frontend/src/styles/global.css`).

All tokens are declared in the `:root` block (lines 15–46) and are available globally
throughout the application. Changes to any token cascade to every component that
references it.

---

## Table of Contents

1. [Colors](#colors)
2. [Typography](#typography)
3. [Spacing](#spacing)
4. [Border Radius](#border-radius)
5. [WCAG Contrast Ratios](#wcag-contrast-ratios)
6. [Contributing](#contributing)

---

## Colors

The palette is dark-mode only. Every text/background pairing meets or exceeds the
WCAG 2.1 AA minimum contrast ratio of 4.5:1 for normal text.

| Token | Value | Tailwind Equivalent | Semantic Purpose |
|-------|-------|---------------------|------------------|
| `--color-bg` | `#0f1117` | — | Page / viewport background |
| `--color-surface` | `#1a1d27` | — | Cards, forms, and raised containers |
| `--color-border` | `#2a2d3a` | — | Dividers, input outlines, progress track background |
| `--color-text-primary` | `#e8eaf0` | — | Body copy, headings, labels |
| `--color-text-secondary` | `#9ca3af` | gray-400 | Subtitles, metadata, placeholder text |
| `--color-accent` | `#4f46e5` | indigo-600 | Primary action button backgrounds (white text on top) |
| `--color-accent-text` | `#818cf8` | indigo-400 | Accent text, badges, focus borders, progress fill |
| `--color-accent-hover` | `#6366f1` | indigo-500 | Hover state for `--color-accent` button backgrounds |
| `--color-error` | `#f87171` | red-400 | Error messages, destructive borders, required-field indicators |
| `--color-warning` | `#fbbf24` | amber-400 | Warning callouts and advisory notices |
| `--color-success` | `#34d399` | emerald-400 | Success states, complete-job download button background |
| `--color-success-hover` | `#6ee7b7` | emerald-300 | Hover state for `--color-success` button backgrounds |

### Usage by Component

| Token | Where Used |
|-------|-----------|
| `--color-bg` | `body`, `.dashboard-main`, `.unseal-main`, form inputs (background), `.skip-link:focus` background |
| `--color-surface` | `.rfc7807-toast`, `.job-card`, `.unseal-card`, `.dashboard-form` |
| `--color-border` | Card/form borders, input outlines, `.job-card__progress-track` background |
| `--color-text-primary` | `body`, headings, labels, card titles, input text |
| `--color-text-secondary` | Subtitles, epoch display, toast detail text, empty-state text, dismiss button |
| `--color-accent` | Submit buttons (`.dashboard-form__submit`, `.unseal-form__submit`), `.error-boundary-fallback__reload`, `.job-card__start-btn` |
| `--color-accent-text` | `.job-card__progress-fill` (default), focused input border color, `.dashboard-pagination__btn`, `:focus-visible` outline |
| `--color-accent-hover` | Hover state on all `--color-accent` buttons |
| `--color-error` | `.rfc7807-toast__title`, error status messages, required-field asterisks, `.job-card__progress-fill--failed`, `.error-boundary-fallback__title` |
| `--color-warning` | Status badge text for warning-state jobs (applied via inline token) |
| `--color-success` | `.job-card__progress-fill--complete`, `.job-card__download-btn` background, `.unseal-status__success` text and border |
| `--color-success-hover` | `.job-card__download-btn:hover` background |

---

## Typography

| Token | Value | Semantic Purpose |
|-------|-------|-----------------|
| `--font-family` | `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif` | Primary typeface stack. Inter is served as a local WOFF2 (air-gapped safe). System fonts are the fallback chain. |
| `--font-size-base` | `1rem` (16px) | Base body font size. All other font sizes in the codebase use `em` or `rem` relative to this value. |
| `--line-height-base` | `1.6` | Unitless line height applied to `body`. WCAG 1.4.12 (Text Spacing) recommends at least 1.5 for body text; 1.6 exceeds this. |

### Font Sizes Used Across Components

The following hardcoded `font-size` values appear in component classes. They are not
tokens today but are documented here for awareness.

| Usage | Value | Location |
|-------|-------|---------|
| Toast title | `0.9375rem` (15px) | `.rfc7807-toast__title` |
| Toast status code | `0.8125rem` (13px) | `.rfc7807-toast__status` |
| Toast detail / form labels | `0.875rem` (14px) | `.rfc7807-toast__detail`, `.dashboard-form__label`, `.unseal-form__label` |
| Base body / inputs / submit buttons | `1rem` (16px) | `body`, inputs, `.dashboard-form__submit` |
| Dashboard section heading | `1.125rem` (18px) | `.dashboard-section__heading` |
| Dashboard header title | `1.75rem` (28px) | `.dashboard-header__title` |
| Unseal card title / error boundary title | `1.5rem` (24px) | `.unseal-card__title`, `.error-boundary-fallback__title` |
| Job status badge | `0.75rem` (12px) | `.job-card__status` |

### Font Weights Used

| Weight | CSS Value | Usage |
|--------|-----------|-------|
| Regular | `400` | Toast status code (`font-weight: 400`) |
| Medium | `500` | Pagination buttons |
| SemiBold | `600` | Labels, card titles, submit buttons, badges, toast titles |
| Bold | `700` | Dashboard header title, unseal card title, error boundary title |

---

## Spacing

All spacing tokens use `rem` units relative to the 16px `--font-size-base`. The scale
follows a non-linear progression designed for tight UI density with readable breathing room.

| Token | Value | Pixels (at 16px base) | When to Use |
|-------|-------|-----------------------|-------------|
| `--spacing-xs` | `0.25rem` | 4px | Inline gaps between tightly related elements (icon + text, badge label gaps); bottom margins on headings within a group |
| `--spacing-sm` | `0.5rem` | 8px | Vertical rhythm between minor elements; horizontal button padding (with `--spacing-md` for horizontal); gap between toast sections |
| `--spacing-md` | `1rem` | 16px | Standard internal component padding; gap between cards in a list; form field gap; toast positioning from viewport edge |
| `--spacing-lg` | `1.5rem` | 24px | Section padding inside form surfaces; unseal-form field bottom margin; button horizontal padding for prominence |
| `--spacing-xl` | `2rem` | 32px | Page-level padding (`body`, `.dashboard-main`); gap between dashboard sections; unseal-card internal padding |

### Decision Guide

```
Inline icon/label gap              → --spacing-xs
Between a heading and its content  → --spacing-xs
Between stacked minor elements     → --spacing-sm
Standard component internal padding → --spacing-md
Between major sections             → --spacing-lg
Page-level margin / hero padding   → --spacing-xl
```

---

## Border Radius

| Token | Value | Pixels (at 16px base) | Semantic Usage |
|-------|-------|-----------------------|----------------|
| `--radius-sm` | `0.375rem` | 6px | Input fields, small buttons (`.job-card__start-btn`, `.job-card__download-btn`, `.error-boundary-fallback__reload`), progress bar fill and track, toast status error regions, `:focus-visible` outline clip |
| `--radius-md` | `0.5rem` | 8px | Cards (`.job-card`, `.unseal-card`), modal-style surfaces (`.rfc7807-toast`), form containers (`.dashboard-form`) |

### Semantic Intent

- **`--radius-sm`** is for interactive controls and contained elements that sit _inside_ a larger surface.
- **`--radius-md`** is for the surfaces themselves — cards, panels, and dialogs.

Never mix radii within the same surface/control hierarchy. An input inside a card should
use `--radius-sm`; the card itself uses `--radius-md`.

---

## WCAG Contrast Ratios

Contrast ratios are calculated using the WCAG 2.1 relative luminance formula
(IEC 61966-2-1 sRGB linearisation). The minimum for normal text is **4.5:1** (AA);
for large text (18pt / 14pt bold) it is **3:1** (AA).

### Text on Background Pairs

| Foreground Token | Background Token | Hex Pair | Contrast Ratio | WCAG AA Normal | WCAG AA Large |
|-----------------|------------------|----------|----------------|:--------------:|:-------------:|
| `--color-text-primary` (#e8eaf0) | `--color-bg` (#0f1117) | `#e8eaf0` on `#0f1117` | ~14.0:1 | Pass | Pass |
| `--color-text-secondary` (#9ca3af) | `--color-bg` (#0f1117) | `#9ca3af` on `#0f1117` | ~5.9:1 | Pass | Pass |
| `--color-text-primary` (#e8eaf0) | `--color-surface` (#1a1d27) | `#e8eaf0` on `#1a1d27` | ~11.7:1 | Pass | Pass |
| `--color-text-secondary` (#9ca3af) | `--color-surface` (#1a1d27) | `#9ca3af` on `#1a1d27` | ~4.9:1 | Pass | Pass |
| `--color-accent-text` (#818cf8) | `--color-bg` (#0f1117) | `#818cf8` on `#0f1117` | ~6.3:1 | Pass | Pass |
| `--color-accent-text` (#818cf8) | `--color-surface` (#1a1d27) | `#818cf8` on `#1a1d27` | ~5.0:1 | Pass | Pass |
| `--color-error` (#f87171) | `--color-bg` (#0f1117) | `#f87171` on `#0f1117` | ~4.8:1 | Pass | Pass |
| `--color-success` (#34d399) | `--color-bg` (#0f1117) | `#34d399` on `#0f1117` | ~8.7:1 | Pass | Pass |
| white (#ffffff) | `--color-accent` (#4f46e5) | `#ffffff` on `#4f46e5` | ~4.5:1 | Pass | Pass |
| `--color-bg` (#0f1117) | `--color-success` (#34d399) | `#0f1117` on `#34d399` | ~9.1:1 | Pass | Pass |
| `--color-bg` (#0f1117) | `--color-success-hover` (#6ee7b7) | `#0f1117` on `#6ee7b7` | ~12.4:1 | Pass | Pass |

### Non-Text (UI Component) Contrast — WCAG 1.4.11 (3:1 minimum)

| Element | Color | Background | Contrast Ratio | WCAG 1.4.11 |
|---------|-------|------------|----------------|:-----------:|
| `:focus-visible` outline (`#818cf8`) | `--color-accent-text` | `--color-surface` (#1a1d27) | ~5.0:1 | Pass |
| `:focus-visible` outline (`#818cf8`) | `--color-bg` (#0f1117) | `--color-bg` (#0f1117) | ~6.3:1 | Pass |
| `--color-border` (#2a2d3a) on `--color-surface` | `--color-border` | `--color-surface` (#1a1d27) | ~1.3:1 | Decorative only — structural dividers exempt under WCAG 1.4.11 |

### Tokens Intentionally Not Used as Text Colors

| Token | Reason |
|-------|--------|
| `--color-accent` (#4f46e5) | Fails WCAG 1.4.3 as text on dark backgrounds (~3:1 on `--color-bg`). Use `--color-accent-text` (#818cf8) instead for any text context. |
| `--color-warning` (#fbbf24) | Used only as status badge text on `--color-surface`. Verify contrast in context before using on new backgrounds. |
| `--color-border` (#2a2d3a) | Purely structural; not a text color. |
| `--color-surface` (#1a1d27) | Container background; not a text color. |
| `--color-accent-hover` (#6366f1) | Hover state only; never a resting text color. |

---

## Contributing

### Adding a New Token

1. **Declare it in `:root`** inside `frontend/src/styles/global.css`.
2. **Follow the naming convention** (see below).
3. **Document it in this file** in the appropriate section table, including value,
   semantic purpose, and all components that use it.
4. **Verify contrast** for any new color token. Add a row to the
   [WCAG Contrast Ratios](#wcag-contrast-ratios) table.
5. **Confirm WCAG compliance** — new text/background pairs must achieve at least
   4.5:1 (normal text) or 3:1 (large text / UI components).

### Naming Convention

Tokens follow a `--<category>-<variant>` pattern:

```
--<category>-<variant>
```

| Category | Variants | Examples |
|----------|----------|---------|
| `color` | Semantic role, then optional state | `--color-bg`, `--color-accent`, `--color-accent-hover`, `--color-text-primary` |
| `font` | Property | `--font-family`, `--font-size-base` |
| `line-height` | Property | `--line-height-base` |
| `spacing` | Scale step (xs, sm, md, lg, xl) | `--spacing-xs`, `--spacing-xl` |
| `radius` | Scale step (sm, md) | `--radius-sm`, `--radius-md` |

**Rules:**

- All names are `kebab-case`.
- Color tokens must encode semantic meaning (e.g., `--color-error`), not raw hue
  names (avoid `--color-red`). This allows palette swaps without renaming.
- State suffixes follow the role: `--color-accent-hover`, not `--color-hover-accent`.
- Numeric scale steps (`xs`, `sm`, `md`, `lg`, `xl`) are preferred over raw numbers
  so the scale can be adjusted without renaming every reference.
- Do NOT introduce a token for a value used in exactly one place with no reuse
  potential — a hardcoded value is more honest than a one-off token.

### Modifying an Existing Token

Before changing a token value:

1. Search the codebase for all usages: `grep -r "var(--your-token" frontend/src/`
2. Re-verify WCAG contrast ratios for every affected text/background pairing.
3. Update this documentation to reflect the new value and any changed contrast ratios.
4. Run `pre-commit run --all-files` to confirm no regressions.
