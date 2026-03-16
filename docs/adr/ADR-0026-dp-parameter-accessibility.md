# ADR-0026 — DP Parameter Accessibility Design Note

**Date:** 2026-03-15
**Status:** Design Note (implementation deferred — no dashboard in current scope)
**Deciders:** PM + UI/UX Reviewer
**Task:** P8-T8.5
**Resolves:** ADV-072 (UI/UX P7-T7.5, ADVISORY — DP parameter dashboard accessibility plan)

---

## Context

Phase 7 introduced DP-SGD synthesis parameters: `epsilon`, `noise_multiplier`,
`max_grad_norm`, and `delta`. These are numerical inputs with non-obvious semantics
that will surface in the React dashboard (ADR-0023) when a future phase implements
the synthesis job creation UI.

The Phase 7 UI/UX review (P7-T7.5) raised ADV-072: no accessibility plan exists for
these inputs. Because no dashboard work was in scope for Phase 7 or Phase 8, this ADR
documents the required ARIA patterns, help text, and tooltip requirements so that the
implementing developer has a complete specification ready when the dashboard task lands.

This document is a **design note**, not an implementation ADR. It contains no code
decisions. Its sole purpose is to ensure ADV-072 is fully drained and the future
dashboard task has an actionable accessibility specification.

---

## DP Parameters Requiring Accessible UI Treatment

| Parameter | Type | Semantics |
|-----------|------|-----------|
| `epsilon` | float (> 0) | Privacy budget. Lower values = stronger privacy = higher noise. Typical range: 0.1 – 10.0. |
| `noise_multiplier` | float (> 0) | Scale of Gaussian noise added to gradients. Higher values = more noise = lower utility. |
| `max_grad_norm` | float (> 0) | Per-sample gradient clipping threshold. Controls sensitivity. |
| `delta` | float (0 < δ << 1) | Failure probability of the (ε, δ)-DP guarantee. Typically 1e-5 to 1e-7. |

These parameters are unfamiliar to most operators. Accessible design requires that:
1. Each input is programmatically associated with a label and a help description.
2. Out-of-range values produce error messages programmatically linked to their input.
3. Keyboard-only users can access explanatory tooltips without pointer interaction.

---

## Required ARIA Patterns

### Pattern 1 — `aria-describedby` for contextual help text

Every DP parameter `<input>` MUST have an `aria-describedby` attribute pointing to a
`<span>` element that contains the parameter's contextual help text. The help text MUST
be persistently rendered (not only visible on hover) so that screen readers announce it
when the input receives focus.

```html
<!-- epsilon input — required pattern -->
<label for="epsilon-input">
  Privacy Budget (epsilon)
</label>
<input
  id="epsilon-input"
  type="number"
  name="epsilon"
  min="0.01"
  step="0.01"
  aria-describedby="epsilon-help epsilon-error"
  aria-required="true"
/>
<span id="epsilon-help" class="field-help">
  Controls the privacy guarantee strength. Lower values provide stronger privacy
  but reduce output quality. Recommended range: 0.5 – 5.0 for most use cases.
</span>
<span id="epsilon-error" class="field-error" role="alert" aria-live="polite">
  <!-- Populated by form validation — empty when no error -->
</span>
```

The same `aria-describedby="<param>-help <param>-error"` pattern MUST be applied to
all four parameters: `epsilon`, `noise_multiplier`, `max_grad_norm`, and `delta`.

### Pattern 2 — Keyboard-accessible tooltips

If a tooltip icon (e.g., an info icon "?") is placed adjacent to the input, it MUST be:
- A `<button>` element (not a `<div>` or `<span>`), so it receives focus via Tab.
- Labelled with `aria-label="Help for [parameter name]"`.
- Toggling a `<div role="tooltip" id="<param>-tooltip">` element on click and on Enter/Space keypress.
- The tooltip trigger button needs only an `aria-label`; do NOT add `aria-describedby` pointing to the tooltip on the button (the input already references it via Pattern 1).

```html
<button
  type="button"
  aria-label="Help for Privacy Budget (epsilon)"
  class="help-icon-btn"
>
  ?
</button>
<div
  id="epsilon-tooltip"
  role="tooltip"
  class="tooltip tooltip--hidden"
>
  Epsilon (ε) is the privacy budget for differential privacy. It quantifies the
  maximum multiplicative difference in the probability of any output between a
  model trained on a dataset with your record vs. without it.
  A value of 1.0 is a common starting point; values below 0.1 are very strong.
</div>
```

The tooltip visibility is toggled via CSS class (e.g., `.tooltip--hidden { visibility: hidden; position: absolute; }`) rather than the HTML `hidden` attribute. The `hidden` attribute removes the element from the accessibility tree, breaking `aria-describedby` linkage. CSS `visibility: hidden` hides the tooltip visually while keeping it accessible to assistive technology when referenced by `aria-describedby`.

**Focus management rules:**
- Focus MUST remain on the trigger `<button>` when the tooltip is shown. The tooltip div is display-only and must not contain focusable children.
- Do not call `focus()` on the tooltip div or set `tabindex` on it.
- The tooltip MUST close when the trigger button loses focus (`onBlur` event), in addition to closing on Escape. This prevents persistent open tooltips from obscuring subsequent form fields for keyboard and screen reader users.

### Pattern 3 — Live validation error messages

Validation errors for DP parameters MUST use `role="alert"` and `aria-live="polite"`
on the error container so that screen readers announce errors without requiring the
user to navigate to the error message.

```html
<span
  id="epsilon-error"
  class="field-error"
  role="alert"
  aria-live="polite"
>
  <!-- Example error text rendered by React state: -->
  <!-- "Epsilon must be greater than 0." -->
</span>
```

The `aria-describedby` on the input MUST reference this error span (as shown in
Pattern 1) so that the error is also announced when the user returns focus to the
input after a validation failure.

### Pattern 4 — Grouping with `<fieldset>` and `<legend>`

All four DP parameters MUST be grouped inside a `<fieldset>` with a `<legend>`:

```html
<fieldset>
  <legend>Differential Privacy Settings</legend>
  <!-- epsilon, noise_multiplier, max_grad_norm, delta inputs go here -->
</fieldset>
```

This provides a semantic grouping that screen readers announce when entering the
section, giving users context before encountering the individual numeric inputs.

---

## Help Text Requirements (Per Parameter)

The following help text is the canonical content for the `aria-describedby` spans.
It MUST be used verbatim (or with only minor editorial improvements) in the
implementing dashboard task.

| Parameter | Help Text |
|-----------|-----------|
| `epsilon` | Controls the privacy guarantee strength. Lower values provide stronger privacy but reduce output quality. Recommended range: 0.5 – 5.0 for most use cases. Must be greater than 0. |
| `noise_multiplier` | Scale of Gaussian noise added to gradients during training. Higher values increase privacy but reduce synthetic data quality. Typical values: 0.5 – 2.0. Must be greater than 0. |
| `max_grad_norm` | Per-sample gradient clipping threshold. Controls the sensitivity of the training process to individual records. Typical values: 0.1 – 5.0. Must be greater than 0. |
| `delta` | The failure probability of the differential privacy guarantee. Should be much smaller than 1/n, where n is the number of training records. Typical values: 1e-5 to 1e-7. Must be between 0 (exclusive) and 1 (exclusive). |

---

## Tooltip Content Requirements (Per Parameter)

Tooltips (Pattern 2) provide deeper explanation for technically minded operators.
The following content is the canonical tooltip text.

| Parameter | Tooltip Text |
|-----------|--------------|
| `epsilon` | Epsilon (ε) is the privacy budget for differential privacy. It quantifies the maximum log-ratio of output probabilities when any single training record is added or removed. Lower ε = stronger privacy = more noise injected during training. |
| `noise_multiplier` | The noise multiplier σ determines the standard deviation of the Gaussian noise added to each gradient update (σ × max_grad_norm). Increasing σ provides stronger privacy at the cost of model utility. |
| `max_grad_norm` | Gradients are clipped to this L2 norm before noise is added. This bounds the influence of any single record on the model update (sensitivity). Smaller values = stricter clipping = lower sensitivity. |
| `delta` | Delta (δ) is the probability that the ε-DP guarantee fails. For a dataset of n records, δ should be set to at most 1/n. The full guarantee is (ε, δ)-DP: the output is indistinguishable up to a factor of eᵉ with probability 1 − δ. |

---

## WCAG 2.1 AA Compliance Checklist

When the implementing developer builds the DP parameter form, the following WCAG 2.1
AA criteria MUST be verified before the task is marked complete:

| Criterion | Requirement | Verification |
|-----------|-------------|--------------|
| 1.3.1 Info and Relationships | All labels and help text are programmatically associated via `<label for>` and `aria-describedby` | axe-core automated scan |
| 1.3.3 Sensory Characteristics | Instructions do not rely solely on shape, color, or visual position | Manual review |
| 2.1.1 Keyboard | All inputs, tooltips, and error states are reachable and operable via keyboard | Manual Tab-through test |
| 2.4.3 Focus Order | Tab order follows visual order (epsilon → noise_multiplier → max_grad_norm → delta) | Manual test |
| 3.3.1 Error Identification | Validation errors identify the field and describe the problem | Manual test |
| 3.3.2 Labels or Instructions | Each input has a visible label and persistent help text | Visual review |
| 4.1.2 Name, Role, Value | All interactive elements have accessible names and roles | axe-core automated scan |
| 4.1.3 Status Messages | Validation errors use `role="alert"` so they are announced without focus | Screen reader test (VoiceOver / NVDA) |

---

## Implementation Notes for the Dashboard Task

1. The React component implementing this form MUST include Vitest + React Testing Library
   tests that assert `aria-describedby` linkage, tooltip keyboard behavior (open on
   Enter, close on Escape), and error message announcement.

2. The `axe-core` accessibility linter MUST be included in the component's test suite
   via `@axe-core/react` or `jest-axe`. Zero axe violations are required at merge time.

3. Tooltip state MUST be managed in React component state (not CSS `:hover`), so the
   keyboard toggle works independently of pointer events.

4. The `<fieldset>` grouping pattern is required regardless of whether the form section
   already has a visible heading. `<legend>` and visible heading serve different semantic
   purposes — both are needed.

---

## Consequences

- ADV-072 is drained. The accessibility gap is now documented with an actionable
  specification.
- The implementing dashboard task (not yet scheduled) MUST reference this ADR and
  implement all four ARIA patterns before the task is marked complete.
- The `ui-ux-reviewer` agent MUST verify pattern compliance at review time for any
  task that touches DP parameter form fields.

---

## References

- ADV-072: UI/UX advisory from P7-T7.5 review — DP parameter dashboard accessibility plan
- ADR-0023: Frontend React/Vite SPA — the dashboard this ADR targets
- ADR-0017: Synthesizer & DP Library Selection — canonical definition of DP parameters
- WCAG 2.1 AA: https://www.w3.org/TR/WCAG21/
- WAI-ARIA Authoring Practices 1.2 — Tooltip Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tooltip/
- CLAUDE.md Accessibility Requirements table — project WCAG 2.1 AA mandate
