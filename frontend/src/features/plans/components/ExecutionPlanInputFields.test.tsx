import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PlanInputDefinition } from "@generated/models";
import {
  ExecutionPlanInputFields,
  executionPlanInputs,
  initialExecutionPlanInputValues,
  requiredExecutionPlanInputsComplete,
} from "@/features/plans/components/ExecutionPlanInputFields";

const DEFINITIONS = [{
  name: "message", input_type: "string", default: "declared", required: false,
  description: "Message supplied to the selected Plan", choices: [], non_empty: false,
}] as PlanInputDefinition[];

function Inputs() {
  const [values, setValues] = useState(() => initialExecutionPlanInputValues(DEFINITIONS));
  return <>
    <ExecutionPlanInputFields definitions={DEFINITIONS} values={values} onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))} />
    <output aria-label="Supplied inputs">{JSON.stringify(executionPlanInputs(values))}</output>
  </>;
}

describe("Plan input provenance", () => {
  it("displays the declaration without supplying it, and preserves explicit equality to the default", () => {
    render(<Inputs />);
    expect(screen.getByLabelText(/message/)).toHaveValue("declared");
    expect(screen.getByLabelText("Supplied inputs")).toHaveTextContent("{}");
    fireEvent.change(screen.getByLabelText("Value source"), { target: { value: "explicit" } });
    expect(screen.getByLabelText("Supplied inputs")).toHaveTextContent('{"message":"declared"}');
    fireEvent.change(screen.getByLabelText("Value source"), { target: { value: "default" } });
    expect(screen.getByLabelText("Supplied inputs")).toHaveTextContent("{}");
    expect(screen.getByLabelText(/message/)).toHaveValue("declared");
  });

  it("keeps an explicitly empty string distinct from explicit null and an omitted value", () => {
    render(<Inputs />);
    fireEvent.change(screen.getByLabelText(/message/), { target: { value: "" } });
    expect(screen.getByLabelText("Supplied inputs")).toHaveTextContent('{"message":""}');
    fireEvent.change(screen.getByLabelText("Value source"), { target: { value: "null" } });
    expect(screen.getByLabelText("Supplied inputs")).toHaveTextContent('{"message":null}');
    expect(screen.getByLabelText(/message/)).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Value source"), { target: { value: "default" } });
    expect(screen.getByLabelText("Supplied inputs")).toHaveTextContent("{}");
  });

  it("does not make an omitted required input complete", () => {
    const required = [{ ...DEFINITIONS[0]!, default: null, required: true }];
    expect(requiredExecutionPlanInputsComplete(required, initialExecutionPlanInputValues(required))).toBe(false);
    expect(requiredExecutionPlanInputsComplete(required, { message: null })).toBe(false);
    expect(requiredExecutionPlanInputsComplete(required, { message: "" })).toBe(false);
    expect(requiredExecutionPlanInputsComplete(required, { message: "selected" })).toBe(true);
  });
});
