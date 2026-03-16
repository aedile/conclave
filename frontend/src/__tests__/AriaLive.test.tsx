/**
 * Vitest unit tests for the AriaLive components.
 *
 * Tests that PoliteAnnouncement and AssertiveAnnouncement render the
 * correct aria-live attribute values and display their children.
 *
 * WCAG 2.1 AA: verifies live region attributes are correct.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  AssertiveAnnouncement,
  PoliteAnnouncement,
} from "../components/AriaLive";

describe("PoliteAnnouncement", () => {
  it("renders children content", () => {
    render(<PoliteAnnouncement>Progress at 50%</PoliteAnnouncement>);
    expect(screen.getByText("Progress at 50%")).toBeInTheDocument();
  });

  it("has aria-live=polite attribute", () => {
    const { container } = render(
      <PoliteAnnouncement>test</PoliteAnnouncement>,
    );
    const region = container.querySelector('[aria-live="polite"]');
    expect(region).toBeInTheDocument();
  });

  it("has aria-atomic=true attribute", () => {
    const { container } = render(
      <PoliteAnnouncement>test</PoliteAnnouncement>,
    );
    const region = container.querySelector('[aria-atomic="true"]');
    expect(region).toBeInTheDocument();
  });

  it("always has the base aria-live-region class", () => {
    const { container } = render(
      <PoliteAnnouncement>test</PoliteAnnouncement>,
    );
    const region = container.querySelector(".aria-live-region");
    expect(region).toBeInTheDocument();
  });

  it("applies optional className", () => {
    const { container } = render(
      <PoliteAnnouncement className="sr-only">test</PoliteAnnouncement>,
    );
    const region = container.querySelector(".sr-only");
    expect(region).toBeInTheDocument();
  });

  it("retains base aria-live-region class when optional className is applied", () => {
    const { container } = render(
      <PoliteAnnouncement className="sr-only">test</PoliteAnnouncement>,
    );
    const region = container.querySelector(".aria-live-region.sr-only");
    expect(region).toBeInTheDocument();
  });
});

describe("AssertiveAnnouncement", () => {
  it("renders children content", () => {
    render(
      <AssertiveAnnouncement>Critical error occurred</AssertiveAnnouncement>,
    );
    expect(screen.getByText("Critical error occurred")).toBeInTheDocument();
  });

  it("has aria-live=assertive attribute", () => {
    const { container } = render(
      <AssertiveAnnouncement>error</AssertiveAnnouncement>,
    );
    const region = container.querySelector('[aria-live="assertive"]');
    expect(region).toBeInTheDocument();
  });

  it("has aria-atomic=true attribute", () => {
    const { container } = render(
      <AssertiveAnnouncement>error</AssertiveAnnouncement>,
    );
    const region = container.querySelector('[aria-atomic="true"]');
    expect(region).toBeInTheDocument();
  });

  it("always has the base aria-live-region class", () => {
    const { container } = render(
      <AssertiveAnnouncement>error</AssertiveAnnouncement>,
    );
    const region = container.querySelector(".aria-live-region");
    expect(region).toBeInTheDocument();
  });

  it("applies optional className", () => {
    const { container } = render(
      <AssertiveAnnouncement className="error-region">
        error
      </AssertiveAnnouncement>,
    );
    const region = container.querySelector(".error-region");
    expect(region).toBeInTheDocument();
  });

  it("retains base aria-live-region class when optional className is applied", () => {
    const { container } = render(
      <AssertiveAnnouncement className="error-region">
        error
      </AssertiveAnnouncement>,
    );
    const region = container.querySelector(".aria-live-region.error-region");
    expect(region).toBeInTheDocument();
  });
});
