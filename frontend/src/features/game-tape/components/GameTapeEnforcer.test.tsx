import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { GameTape } from "@generated/models";
import { GameTapeEnforcer } from "@/features/game-tape/components/GameTapeEnforcer";
import { operations, operationUrl } from "@generated/operations";

const receiptPath = Object.getOwnPropertyDescriptor(operations.get_fleet_run_enforcer_receipt, "path")!;
afterEach(() => Object.defineProperty(operations.get_fleet_run_enforcer_receipt, "path", receiptPath));

describe("GameTapeEnforcer", () => {
  it.each([false, true])("uses the generated receipt path after contract changes: %s", (changedPath) => {
    if (changedPath) Object.defineProperty(operations.get_fleet_run_enforcer_receipt, "path", {
      value: "/recorded-receipts/{fleet_run_id}/enforcer",
    });
    const runId = "recorded run ?#snow";
    render(<GameTapeEnforcer tape={{ fleet_run_id: runId, enforcer_receipt_available: true } as GameTape} />);
    expect(screen.getByRole("link", { name: "Download enforcer.yml" })).toHaveAttribute(
      "href", `/api${operationUrl(operations.get_fleet_run_enforcer_receipt.operationId, { path: { fleet_run_id: runId } })}`,
    );
  });

  it("uses backend receipt availability instead of reconstructing CI/CD policy", () => {
    render(<GameTapeEnforcer tape={{ fleet_run_id: "run-1", enforcer_receipt_available: true } as GameTape} />);
    const link = screen.getByRole("link", { name: "Download enforcer.yml" });
    expect(link.getAttribute("href")).toBe("/api/fleet/runs/run-1/enforcer.yml");
  });

  it("states when the backend says no receipt exists", () => {
    render(<GameTapeEnforcer tape={{ fleet_run_id: "run-2", enforcer_receipt_available: false } as GameTape} />);
    expect(screen.getByText("This run does not have an Enforcer receipt.")).toBeTruthy();
  });
});
