/**
 * Vitest unit tests for the CreateJobForm component.
 *
 * Tests prop-driven rendering, form field labelling, accessibility attributes
 * (aria-required, aria-invalid, aria-describedby, aria-hidden asterisks), and
 * callback invocation for onChange and onSubmit.
 *
 * All WCAG 2.1 AA attributes are verified here so that extraction from
 * Dashboard.tsx does not silently drop accessibility requirements (P27-T27.2).
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Import AFTER mock setup (component does not use API client directly)
// ---------------------------------------------------------------------------

import CreateJobForm, {
  type CreateJobFormState,
} from "../components/CreateJobForm";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const emptyForm: CreateJobFormState = {
  table_name: "",
  parquet_path: "",
  total_epochs: "",
  checkpoint_every_n: "",
};

const filledForm: CreateJobFormState = {
  table_name: "customers",
  parquet_path: "/data/customers.parquet",
  total_epochs: "10",
  checkpoint_every_n: "2",
};

// ---------------------------------------------------------------------------
// Tests — rendering
// ---------------------------------------------------------------------------

describe("CreateJobForm — rendering", () => {
  it("renders all four labelled inputs", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/table name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/parquet path/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/total epochs/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/checkpoint every/i)).toBeInTheDocument();
  });

  it("renders Create Job submit button", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("button", { name: /create job/i }),
    ).toBeInTheDocument();
  });

  it("shows Creating… and disables button when isCreating is true", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={true}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const btn = screen.getByRole("button", { name: /creating/i });
    expect(btn).toBeDisabled();
  });

  it("populates inputs from the form prop", () => {
    render(
      <CreateJobForm
        form={filledForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/table name/i)).toHaveValue("customers");
    expect(screen.getByLabelText(/parquet path/i)).toHaveValue(
      "/data/customers.parquet",
    );
    expect(screen.getByLabelText(/total epochs/i)).toHaveValue(10);
    expect(screen.getByLabelText(/checkpoint every/i)).toHaveValue(2);
  });

  it("displays form validation error message when provided", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Total Epochs must be a valid integer."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/total epochs must be a valid integer/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tests — accessibility attributes (WCAG 2.1 AA)
// ---------------------------------------------------------------------------

describe("CreateJobForm — accessibility attributes", () => {
  it("all four inputs have aria-required='true'", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/table name/i)).toHaveAttribute(
      "aria-required",
      "true",
    );
    expect(screen.getByLabelText(/parquet path/i)).toHaveAttribute(
      "aria-required",
      "true",
    );
    expect(screen.getByLabelText(/total epochs/i)).toHaveAttribute(
      "aria-required",
      "true",
    );
    expect(screen.getByLabelText(/checkpoint every/i)).toHaveAttribute(
      "aria-required",
      "true",
    );
  });

  it("total_epochs input has aria-invalid='true' when formErrorField is total_epochs", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Total Epochs must be a valid integer."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/total epochs/i)).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("checkpoint_every_n input has aria-invalid='true' when formErrorField is checkpoint_every_n", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Checkpoint Every must be a valid integer."
        formErrorField="checkpoint_every_n"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/checkpoint every/i)).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("aria-invalid is false on inputs when formErrorField is null", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/total epochs/i)).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText(/checkpoint every/i)).not.toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("total_epochs input has aria-describedby='form-error' when it is the error field", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Total Epochs must be a valid integer."
        formErrorField="total_epochs"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/total epochs/i)).toHaveAttribute(
      "aria-describedby",
      "form-error",
    );
  });

  it("checkpoint_every_n input has aria-describedby='form-error' when it is the error field", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError="Checkpoint Every must be a valid integer."
        formErrorField="checkpoint_every_n"
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByLabelText(/checkpoint every/i)).toHaveAttribute(
      "aria-describedby",
      "form-error",
    );
  });

  it("asterisk required indicators are wrapped with aria-hidden='true'", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const hiddenAsterisks = document.querySelectorAll(
      'form span[aria-hidden="true"]',
    );
    expect(hiddenAsterisks.length).toBeGreaterThanOrEqual(4);
    hiddenAsterisks.forEach((span) => {
      expect(span.textContent).toBe("*");
    });
  });

  it("form-error container is always in the DOM (NVDA+Firefox swallow-repeat guard)", () => {
    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    const errorDiv = document.querySelector("#form-error");
    expect(errorDiv).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tests — callbacks
// ---------------------------------------------------------------------------

describe("CreateJobForm — callbacks", () => {
  it("calls onFormChange with correct field and value when table_name input changes", async () => {
    const user = userEvent.setup();
    const onFormChange = vi.fn();

    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={onFormChange}
        onSubmit={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText(/table name/i), "orders");

    await waitFor(() => {
      expect(onFormChange).toHaveBeenCalledWith("table_name", expect.any(String));
    });
  });

  it("calls onFormChange with correct field and value when parquet_path input changes", async () => {
    const user = userEvent.setup();
    const onFormChange = vi.fn();

    render(
      <CreateJobForm
        form={emptyForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={onFormChange}
        onSubmit={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText(/parquet path/i), "/data/orders.parquet");

    await waitFor(() => {
      expect(onFormChange).toHaveBeenCalledWith("parquet_path", expect.any(String));
    });
  });

  it("calls onSubmit when the form is submitted", async () => {
    const onSubmit = vi.fn((e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
    });

    render(
      <CreateJobForm
        form={filledForm}
        isCreating={false}
        formValidationError={null}
        formErrorField={null}
        onFormChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const form = document.querySelector("form")!;
    fireEvent.submit(form);

    expect(onSubmit).toHaveBeenCalledOnce();
  });
});
