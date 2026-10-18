import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FleetCommandCenter } from "@/features/commands/components/FleetCommandCenter";

describe("FleetCommandCenter", () => {
  it("requires the operator to enter a task explicitly instead of preloading an executable", () => {
    const onRun = vi.fn();
    render(<FleetCommandCenter center={null} actionsDisabled={false} onRun={onRun} />);

    const input = screen.getByLabelText("Executable") as HTMLInputElement;
    const run = screen.getByRole("button", { name: "Run task" });
    expect(input.value).toBe("");
    expect(run).toBeDisabled();

    fireEvent.change(input, { target: { value: "pytest" } });
    expect(run).toBeDisabled();
  });

  it("runs the displayed fleet scope instead of ambient all repositories", () => {
    const onRun = vi.fn();
    render(<FleetCommandCenter center={{
      durable_revision: 8,
      journal_revision: 4,
      repository_ids: ["repo-a", "repo-b"],
      repository_instance_ids: { "repo-a": "instance-a", "repo-b": "instance-b" },
      recipes: [{
        spec: { capability_id: "uv.recipe.lock-check", recipe: "LOCK_CHECK", label: "Lock check", description: "Check", safety: "READ_ONLY" },
        successful: 2, not_run: 0, attention_repository_ids: [], stale_repository_ids: [],
        latest_run_id: null, latest_outcome: null, needs_attention: 0, stale: 0, repositories: 2,
      }],
    }} actionsDisabled={false} onRun={onRun} />);

    fireEvent.click(screen.getByRole("button", { name: "Run on 2 repos" }));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ recipe: "LOCK_CHECK" }), ["repo-a", "repo-b"]);
  });
});
