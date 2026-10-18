import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FleetExecutorPoolSnapshot, PlacementQueueSnapshot } from "@generated/models";
import { ExecutorOperations } from "@/features/operations/components/ExecutorOperations";

const EXECUTORS = {
  executors: [{
    executor_id: "executor-a",
    generation: 7,
    priority: 10,
    backend_id: "opensandbox",
    admission_mode: "active",
    trust_state: "trusted",
    active_fleet_run_ids: [],
    operational_revision: 2,
    accepting_new_runs: true,
  }],
} as unknown as FleetExecutorPoolSnapshot;

const QUEUE = {
  entries: [{ state: "waiting", fleet_run_id: "run-waiting", sequence: 11, position: 1 }],
} as unknown as PlacementQueueSnapshot;

describe("ExecutorOperations", () => {
  it("keeps explicit executor and queued-admission controls visible", () => {
    const onDrain = vi.fn();
    const onRevokeTrust = vi.fn();
    const onCancelQueued = vi.fn();
    render(<ExecutorOperations
      executors={EXECUTORS}
      placementQueue={QUEUE}
      actionsDisabled={false}
      onDrain={onDrain}
      onActivate={vi.fn()}
      onRevokeTrust={onRevokeTrust}
      onRestoreTrust={vi.fn()}
      onCancelQueued={onCancelQueued}
      onRefresh={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Drain" }));
    fireEvent.click(screen.getByRole("button", { name: "Revoke trust" }));
    expect(onRevokeTrust).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm revoke trust" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel queued admission" }));

    expect(onDrain).toHaveBeenCalledWith(expect.objectContaining({ executor_id: "executor-a", generation: 7, operational_revision: 2 }));
    expect(onRevokeTrust).toHaveBeenCalledWith(expect.objectContaining({ executor_id: "executor-a", generation: 7, operational_revision: 2 }));
    expect(onCancelQueued).toHaveBeenCalledWith(expect.objectContaining({ fleet_run_id: "run-waiting" }));
  });
});
