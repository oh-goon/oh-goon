import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SharedPlanInputInspection } from "@generated/models";
import { PlanInputDiagnostic } from "@/components/common/PlanInputDiagnostic";

const rejection = {
  schema: "oh-goon.shared-plan-input-inspection/v1", status: "rejected",
  plan_key: null, sha256: null, plan_id: null, execution: "not_started_by_inspection",
  diagnostic: {
    schema: "oh-goon.plan-validation-diagnostic/v1", status: "rejected", stage: "input_contract",
    code: "INVALID_INPUT_VALUE", inputs: ["coverage_threshold"],
    safe_next_action: "Correct the caller input value.", execution: "not_started_by_diagnostic",
  },
} as SharedPlanInputInspection;

describe("T5 safe UI diagnostic", () => {
  it("only presents canonical diagnostic metadata and no execution promise", () => {
    render(<PlanInputDiagnostic inspection={rejection} />);
    expect(screen.getByRole("alert")).toHaveTextContent("INVALID_INPUT_VALUE");
    expect(screen.getByRole("alert")).toHaveTextContent("coverage_threshold");
    expect(screen.getByRole("alert")).toHaveTextContent("No execution was started");
  });
  it("never renders a success as RUN_ADMITTED", () => {
    const accepted = { ...rejection, status: "accepted", diagnostic: null, plan_key: "quality", sha256: "a".repeat(64), plan_id: "p" } as SharedPlanInputInspection;
    const { container } = render(<PlanInputDiagnostic inspection={accepted} />);
    expect(container.textContent).toBe("");
  });
  it("does not echo an absent report", () => {
    const { container } = render(<PlanInputDiagnostic inspection={null} />);
    expect(container.textContent).toBe("");
  });
});
