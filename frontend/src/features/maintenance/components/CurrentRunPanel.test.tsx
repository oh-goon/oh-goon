import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FleetRunStatus, OperationAction } from "@generated/enums";
import type { FleetRunState } from "@generated/models";
import { CurrentRunPanel } from "@/features/maintenance/components/CurrentRunPanel";

function run(id: string, status: FleetRunState["status"]): FleetRunState {
  return {
    fleet_run_id: id,
    status,
    capability: { action: OperationAction.INIT },
    progress: { completed: 0, total: 1, succeeded: 0, failed: 0, needs_attention: 0 },
    targets: [{ repository_id: "repo-a", name: "repo-a", path: "/repo-a" }],
  } as FleetRunState;
}

describe("CurrentRunPanel Run ownership", () => {
  it("binds destructive confirmation to the exact displayed Run identity", () => {
    const onCancel = vi.fn();
    const view = render(<CurrentRunPanel
      run={run("run-a", FleetRunStatus.RUNNING)}
      detail={null}
      mode="active"
      onCancel={onCancel}
      onInspect={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("button", { name: "Confirm cancel" })).toBeVisible();

    view.rerender(<CurrentRunPanel
      run={run("run-b", FleetRunStatus.RUNNING)}
      detail={null}
      mode="active"
      onCancel={onCancel}
      onInspect={vi.fn()}
    />);

    expect(screen.queryByRole("button", { name: "Confirm cancel" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledWith("run-b");
  });

  it("does not resurrect an old confirmation across an A to B to A identity transition", () => {
    const onCancel = vi.fn();
    const view = render(<CurrentRunPanel
      run={run("run-a", FleetRunStatus.RUNNING)}
      detail={null}
      mode="active"
      onCancel={onCancel}
      onInspect={vi.fn()}
    />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(screen.getByRole("button", { name: "Confirm cancel" })).toBeVisible();

    view.rerender(<CurrentRunPanel
      run={run("run-b", FleetRunStatus.RUNNING)}
      detail={null}
      mode="active"
      onCancel={onCancel}
      onInspect={vi.fn()}
    />);
    view.rerender(<CurrentRunPanel
      run={run("run-a", FleetRunStatus.RUNNING)}
      detail={null}
      mode="active"
      onCancel={onCancel}
      onInspect={vi.fn()}
    />);

    expect(screen.queryByRole("button", { name: "Confirm cancel" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeVisible();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("renders terminal event state without exposing a cancel action when final detail is unavailable", () => {
    render(<CurrentRunPanel
      run={run("run-a", FleetRunStatus.CANCELLED)}
      detail={null}
      mode="completed"
      onInspect={vi.fn()}
    />);

    expect(screen.getByRole("heading", { name: "Run result" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel run" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open in Runs" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Repository details are temporarily unavailable");
    expect(screen.queryByText("Not completed")).not.toBeInTheDocument();
  });
});
