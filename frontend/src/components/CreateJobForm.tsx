/**
 * CreateJobForm — job creation form sub-component.
 *
 * Extracted from Dashboard.tsx (P27-T27.2). This component is a pure
 * presentational form: it receives all state and callbacks as props.
 * Dashboard owns the state and validation logic; CreateJobForm only renders.
 *
 * CONSTITUTION:
 *   - WCAG 2.1 AA: all form inputs carry aria-required="true".
 *   - Integer fields (total_epochs, checkpoint_every_n) receive
 *     aria-invalid="true" when formErrorField matches, and
 *     aria-describedby="form-error" for programmatic association (WCAG 3.3.1).
 *   - Visible asterisks in labels are wrapped with aria-hidden="true" so
 *     screen readers rely on aria-required instead of reading "*".
 *   - The form-error container is always in the DOM (never conditionally
 *     mounted) so NVDA+Firefox does not swallow repeat announcements when
 *     the same validation error fires twice in a row.
 *   - No inline style= attributes — all layout via CSS classes (P20-T20.3 AC3).
 *
 * P27-T27.3: "Create Job" submit button replaced with AsyncButton component
 * for standardized loading/disabled pattern. Layout class (.dashboard-form__submit)
 * passed via className to preserve existing visual presentation.
 */

import type React from "react";
import AsyncButton from "./AsyncButton";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Shape of the Create Job form fields managed by Dashboard. */
export interface CreateJobFormState {
  table_name: string;
  parquet_path: string;
  total_epochs: string;
  checkpoint_every_n: string;
}

/** Props for the CreateJobForm component. */
interface CreateJobFormProps {
  /** Current form field values. */
  form: CreateJobFormState;
  /** Whether job creation is in-flight (disables submit, shows "Creating…"). */
  isCreating: boolean;
  /** Validation error message to display, or null when form is valid. */
  formValidationError: string | null;
  /** Which field triggered the validation error, or null when form is valid. */
  formErrorField: keyof CreateJobFormState | null;
  /** Callback invoked on every input change. Dashboard updates its form state. */
  onFormChange: (field: keyof CreateJobFormState, value: string) => void;
  /** Callback invoked when the form is submitted. Dashboard owns validation. */
  onSubmit: (e: React.FormEvent<HTMLFormElement>) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Render the Create Job form.
 *
 * All state is controlled externally — this component has no internal state.
 *
 * @param form - Current form field values.
 * @param isCreating - When true, disables the submit button and shows "Creating…".
 * @param formValidationError - Error message to display, or null.
 * @param formErrorField - The field that triggered the error, for aria-invalid wiring.
 * @param onFormChange - Called with (field, value) on every input change event.
 * @param onSubmit - Called with the form submit event. Caller must call e.preventDefault().
 */
export default function CreateJobForm({
  form,
  isCreating,
  formValidationError,
  formErrorField,
  onFormChange,
  onSubmit,
}: CreateJobFormProps): JSX.Element {
  return (
    <section aria-labelledby="create-job-heading">
      <h2
        id="create-job-heading"
        className="dashboard-section__heading"
      >
        Create Job
      </h2>

      <form
        onSubmit={onSubmit}
        className="dashboard-form"
      >
        {/* Form validation error — id="form-error" enables aria-describedby
            association from the triggering input field (WCAG 1.3.1).
            WCAG FIX: Container is always in the DOM so NVDA+Firefox does not
            swallow repeat announcements when the same error fires twice.
            Only the text content is conditional; padding collapses to 0 when
            empty so the layout is unaffected. */}
        <div
          id="form-error"
          role="alert"
          className={`dashboard-form__error${formValidationError !== null ? " dashboard-form__error--active" : ""}`}
        >
          {formValidationError}
        </div>

        <div className="dashboard-form__field">
          <label
            htmlFor="table-name"
            className="dashboard-form__label"
          >
            Table Name{" "}
            {/* Visible required indicator — aria-hidden so SR reads aria-required */}
            <span aria-hidden="true" className="dashboard-form__required-indicator">
              *
            </span>
          </label>
          <input
            id="table-name"
            type="text"
            required
            aria-required="true"
            value={form.table_name}
            onChange={(e) => onFormChange("table_name", e.target.value)}
            placeholder="e.g. customers"
            className="dashboard-form__input"
          />
        </div>

        <div className="dashboard-form__field">
          <label
            htmlFor="parquet-path"
            className="dashboard-form__label"
          >
            Parquet Path{" "}
            {/* Visible required indicator — aria-hidden so SR reads aria-required */}
            <span aria-hidden="true" className="dashboard-form__required-indicator">
              *
            </span>
          </label>
          <input
            id="parquet-path"
            type="text"
            required
            aria-required="true"
            value={form.parquet_path}
            onChange={(e) => onFormChange("parquet_path", e.target.value)}
            placeholder="e.g. /data/customers.parquet"
            className="dashboard-form__input"
          />
        </div>

        <div className="dashboard-form__field">
          <label
            htmlFor="total-epochs"
            className="dashboard-form__label"
          >
            Total Epochs{" "}
            {/* Visible required indicator — aria-hidden so SR reads aria-required */}
            <span aria-hidden="true" className="dashboard-form__required-indicator">
              *
            </span>
          </label>
          <input
            id="total-epochs"
            type="number"
            required
            min={1}
            aria-required="true"
            aria-invalid={formErrorField === "total_epochs"}
            value={form.total_epochs}
            onChange={(e) => onFormChange("total_epochs", e.target.value)}
            placeholder="e.g. 100"
            aria-describedby={
              formErrorField === "total_epochs" ? "form-error" : undefined
            }
            className="dashboard-form__input"
          />
        </div>

        <div className="dashboard-form__field">
          <label
            htmlFor="checkpoint-every"
            className="dashboard-form__label"
          >
            Checkpoint Every (epochs){" "}
            {/* Visible required indicator — aria-hidden so SR reads aria-required */}
            <span aria-hidden="true" className="dashboard-form__required-indicator">
              *
            </span>
          </label>
          <input
            id="checkpoint-every"
            type="number"
            required
            min={1}
            aria-required="true"
            aria-invalid={formErrorField === "checkpoint_every_n"}
            value={form.checkpoint_every_n}
            onChange={(e) =>
              onFormChange("checkpoint_every_n", e.target.value)
            }
            placeholder="e.g. 10"
            aria-describedby={
              formErrorField === "checkpoint_every_n" ? "form-error" : undefined
            }
            className="dashboard-form__input"
          />
        </div>

        {/*
         * P27-T27.3: "Create Job" submit replaced with AsyncButton.
         * className preserves layout styling; loading state standardized.
         */}
        <div className="dashboard-form__actions">
          <AsyncButton
            type="submit"
            isLoading={isCreating}
            loadingText="Creating…"
            className="dashboard-form__submit"
          >
            Create Job
          </AsyncButton>
        </div>
      </form>
    </section>
  );
}
