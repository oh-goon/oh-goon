import { fireEvent, render, screen, within } from "@testing-library/react";

import type { PlanConfigurationEvidence } from "@generated/models";
import { PlanValueNamespace } from "@generated/enums";
import { GameTapeConfiguration } from "@/features/game-tape/components/GameTapeConfiguration";

describe("GameTapeConfiguration", () => {
  it("shows retained parent and source decisions without inventing values", () => {
    render(<GameTapeConfiguration configuration={{ entries: [{
      scope: "first", namespace: PlanValueNamespace.INPUT, name: "project", value_state: "defined",
      source_sha256: "a".repeat(64), line: 3, column: 2,
      binding: { kind: "parent_input", parent_input: "selected_project", candidates: [
        { kind: "env", name: "PROJECT", state: "missing" }, { kind: "yaml", state: "not_evaluated" },
      ] },
    }] }} />);
    fireEvent.click(screen.getByText("Configuration explanation"));
    expect(screen.getByText("first.input.project")).toBeTruthy();
    expect(screen.getByText("<root>.selected_project")).toBeTruthy();
    expect(screen.getByText("a".repeat(64) + ":3:2")).toBeTruthy();
    expect(screen.getByText("env:PROJECT")).toBeTruthy();
    expect(screen.getByText(/Values are withheld/)).toBeTruthy();
  });

  it("keeps root and an import named root distinct, including forwarded parent inputs", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      render(<GameTapeConfiguration configuration={{ entries: [
        { scope: null, namespace: PlanValueNamespace.INPUT, name: "project", value_state: "defined", binding: { kind: "caller" } },
        { scope: "root", namespace: PlanValueNamespace.INPUT, name: "project", value_state: "defined", binding: { kind: "parent_input", parent_scope: null, parent_input: "project" } },
        { scope: "root.child", namespace: PlanValueNamespace.INPUT, name: "project", value_state: "defined", binding: { kind: "parent_input", parent_scope: "root", parent_input: "project" } },
      ] }} />);
      fireEvent.click(screen.getByText("Configuration explanation"));
      expect(screen.getByText("<root>.input.project")).toBeTruthy();
      const forwardedFromRoot = screen.getByText("root.input.project").closest("li");
      const forwardedFromImport = screen.getByText("root.child.input.project").closest("li");
      expect(forwardedFromRoot?.textContent).toContain("<root>.project");
      expect(forwardedFromImport?.textContent).toContain("root.project");
      expect(errors).not.toHaveBeenCalled();
    } finally {
      errors.mockRestore();
    }
  });

  it("keeps every retained decision reachable with bounded pages and reports omissions", () => {
    const configuration: PlanConfigurationEvidence = { omitted_entries: 4, entries: Array.from({ length: 51 }, (_, index) => ({
      namespace: PlanValueNamespace.INPUT, name: `input_${index}`, value_state: "defined", binding: { kind: "default" },
    })) };
    render(<GameTapeConfiguration configuration={configuration} />);
    fireEvent.click(screen.getByText("Configuration explanation"));
    expect(screen.queryByText("<root>.input.input_50")).toBeNull();
    const pagination = screen.getByRole("navigation", { name: "Configuration decision pages" });
    fireEvent.click(within(pagination).getByRole("button", { name: "Next" }));
    expect(screen.getByText("<root>.input.input_50")).toBeTruthy();
    expect(screen.queryByText("<root>.input.input_0")).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("4");
    fireEvent.click(within(pagination).getByRole("button", { name: "Previous" }));
    expect(screen.getByText("<root>.input.input_0")).toBeTruthy();
  });

  it("distinguishes unavailable historical evidence from an empty captured trace", () => {
    const { rerender } = render(<GameTapeConfiguration />);
    expect(screen.getByText("Configuration lineage was not retained for this Run.")).toBeTruthy();
    rerender(<GameTapeConfiguration configuration={{ entries: [] }} />);
    expect(screen.queryByText("Configuration lineage was not retained for this Run.")).toBeNull();
    expect(screen.getByText("No input or environment decisions were captured.")).toBeTruthy();
  });
});
