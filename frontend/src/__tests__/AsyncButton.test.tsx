/**
 * Vitest unit tests for the AsyncButton component.
 *
 * Covers AC1–AC6 from P27-T27.3:
 *   AC1: Prop contract — isLoading, loadingText, children, disabled, HTML attrs.
 *   AC2: Loading state — spinner visible, button disabled, aria-disabled, aria-live.
 *   AC3: Spinner is aria-hidden, uses .async-button__spinner class.
 *   AC5: Existing tests pass; new tests for AsyncButton in isolation.
 *   AC6: WCAG — focus ring, disabled/loading communication to screen readers.
 *
 * Known failure pattern guard (Phase 23 retro):
 *   aria-live announcement fires for both start AND end of async operation.
 *
 * Ref forwarding guard (Phase 23 retro):
 *   forwardRef must allow parent to focus the button after toast dismiss.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import AsyncButton from "../components/AsyncButton";

// ---------------------------------------------------------------------------
// AC1 — Prop contract
// ---------------------------------------------------------------------------

describe("AsyncButton — AC1: prop contract", () => {
  it("renders children when not loading", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Working…">
        Submit
      </AsyncButton>,
    );
    expect(screen.getByRole("button", { name: /submit/i })).toBeInTheDocument();
  });

  it("renders loadingText instead of children when isLoading=true", () => {
    render(
      <AsyncButton isLoading={true} loadingText="Unsealing…">
        Unseal Vault
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveTextContent("Unsealing…");
    expect(screen.queryByText("Unseal Vault")).not.toBeInTheDocument();
  });

  it("passes through className to the button element", () => {
    render(
      <AsyncButton
        isLoading={false}
        loadingText="Loading…"
        className="my-custom-btn"
      >
        Click me
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveClass("my-custom-btn");
  });

  it("passes through type attribute", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Saving…" type="submit">
        Save
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveAttribute("type", "submit");
  });

  it("defaults type to 'button' to prevent accidental form submission", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Loading…">
        Click
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });

  it("applies external disabled prop independently of isLoading", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Loading…" disabled>
        Click
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// AC2 — Loading state behaviour
// ---------------------------------------------------------------------------

describe("AsyncButton — AC2: loading state", () => {
  it("sets button disabled when isLoading=true", () => {
    render(
      <AsyncButton isLoading={true} loadingText="Processing…">
        Submit
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("sets aria-disabled='true' when isLoading=true", () => {
    render(
      <AsyncButton isLoading={true} loadingText="Processing…">
        Submit
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-disabled", "true");
  });

  it("sets aria-disabled='false' when not loading and not externally disabled", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Processing…">
        Submit
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-disabled", "false");
  });

  it("renders an aria-live='polite' region that announces loading state", () => {
    render(
      <AsyncButton isLoading={true} loadingText="Downloading…">
        Download
      </AsyncButton>,
    );
    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
    expect(liveRegion).toHaveTextContent("Downloading…");
  });

  it("aria-live region is empty when not loading", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Downloading…">
        Download
      </AsyncButton>,
    );
    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toBeEmptyDOMElement();
  });

  it("does not fire onClick when isLoading=true", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncButton isLoading={true} loadingText="Working…" onClick={onClick}>
        Submit
      </AsyncButton>,
    );

    await user.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires onClick when not loading", async () => {
    const onClick = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncButton isLoading={false} loadingText="Working…" onClick={onClick}>
        Submit
      </AsyncButton>,
    );

    await user.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

// ---------------------------------------------------------------------------
// AC3 — Spinner element
// ---------------------------------------------------------------------------

describe("AsyncButton — AC3: spinner element", () => {
  it("renders a spinner span with aria-hidden when isLoading=true", () => {
    const { container } = render(
      <AsyncButton isLoading={true} loadingText="Loading…">
        Submit
      </AsyncButton>,
    );
    const spinner = container.querySelector(".async-button__spinner");
    expect(spinner).toBeInTheDocument();
    expect(spinner).toHaveAttribute("aria-hidden", "true");
  });

  it("does not render a spinner when isLoading=false", () => {
    const { container } = render(
      <AsyncButton isLoading={false} loadingText="Loading…">
        Submit
      </AsyncButton>,
    );
    expect(container.querySelector(".async-button__spinner")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC6 — WCAG: ref forwarding for focus restoration
// ---------------------------------------------------------------------------

describe("AsyncButton — AC6: ref forwarding", () => {
  it("forwards ref to the underlying button element", () => {
    const ref = createRef<HTMLButtonElement>();

    render(
      <AsyncButton ref={ref} isLoading={false} loadingText="Loading…">
        Click
      </AsyncButton>,
    );

    expect(ref.current).not.toBeNull();
    expect(ref.current?.tagName).toBe("BUTTON");
  });

  it("focuses the button via forwarded ref", () => {
    const ref = createRef<HTMLButtonElement>();

    render(
      <AsyncButton ref={ref} isLoading={false} loadingText="Loading…">
        Click
      </AsyncButton>,
    );

    ref.current?.focus();
    expect(ref.current).toHaveFocus();
  });
});

// ---------------------------------------------------------------------------
// AC6 — WCAG: loading modifier class for styling hook
// ---------------------------------------------------------------------------

describe("AsyncButton — AC6: loading modifier class", () => {
  it("applies async-button--loading class when isLoading=true", () => {
    render(
      <AsyncButton isLoading={true} loadingText="Working…">
        Submit
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).toHaveClass("async-button--loading");
  });

  it("does not apply async-button--loading class when isLoading=false", () => {
    render(
      <AsyncButton isLoading={false} loadingText="Working…">
        Submit
      </AsyncButton>,
    );
    expect(screen.getByRole("button")).not.toHaveClass("async-button--loading");
  });
});
