import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EMPTY_SHARED_PLAN_AGENT, SharedPlanAgentFields, sharedPlanAgentSelection, sharedPlanRepairSelection } from "@/features/league/components/SharedPlanAgentFields";

function Selection() {
  const [draft, setDraft] = useState(EMPTY_SHARED_PLAN_AGENT);
  return <>
    <SharedPlanAgentFields draft={draft} onChange={setDraft} />
    <output aria-label="Agent selection">{JSON.stringify(sharedPlanAgentSelection(draft))}</output>
    <output aria-label="Repair selection">{JSON.stringify(sharedPlanRepairSelection(draft))}</output>
  </>;
}

describe("Shared Plan Agent selections", () => {
  it("uses the public model/profile selection and gives native implementations their model authority", () => {
    render(<Selection />);
    fireEvent.click(screen.getByText("Agent and bounded repair selection"));
    fireEvent.click(screen.getByLabelText("Select an Agent workflow"));
    fireEvent.change(screen.getByLabelText("Acceptance profile"), { target: { value: "project-check" } });
    fireEvent.change(screen.getByLabelText("Repository root (absolute)"), { target: { value: "/work/repos" } });
    fireEvent.change(screen.getByLabelText("Skill root (absolute)"), { target: { value: "/work/workflow" } });
    fireEvent.change(screen.getByLabelText("Model profile (provider workflows)"), { target: { value: "selected-model" } });
    expect(JSON.parse(screen.getByLabelText("Agent selection").textContent!)).toMatchObject({
      model_profile: "selected-model", acceptance: "project-check", repository_root: "/work/repos", skill_root: "/work/workflow",
    });
    fireEvent.change(screen.getByLabelText("Agent implementation"), { target: { value: "codex" } });
    fireEvent.change(screen.getByLabelText("Native profile"), { target: { value: "selected-native" } });
    expect(JSON.parse(screen.getByLabelText("Agent selection").textContent!)).toMatchObject({ implementation: "codex", profile: "selected-native", model_profile: null });
    expect(screen.queryByLabelText("Model profile (provider workflows)")).toBeNull();
  });

  it("transports the caller's exact bounded repair limits and ordered write roots", () => {
    render(<Selection />);
    fireEvent.click(screen.getByText("Agent and bounded repair selection"));
    fireEvent.click(screen.getByLabelText("Select an Agent workflow"));
    fireEvent.click(screen.getByLabelText("Run the complete bounded review-and-repair campaign"));
    fireEvent.change(screen.getByLabelText("Maximum review passes"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Authorized write roots (one per line)"), { target: { value: "src\ntests\nsrc" } });
    fireEvent.change(screen.getByLabelText("Campaign timeout (seconds)"), { target: { value: "240" } });
    fireEvent.change(screen.getByLabelText("Step timeout (seconds)"), { target: { value: "30" } });
    expect(JSON.parse(screen.getByLabelText("Repair selection").textContent!)).toEqual({ review_passes: 3, write_roots: ["src", "tests", "src"], timeout_seconds: 240, step_timeout_seconds: 30 });
  });
});
