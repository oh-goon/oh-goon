import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GameTape } from "@generated/models";
import { GameTapeEnforcer } from "@/features/game-tape/components/GameTapeEnforcer";

describe("GameTapeEnforcer", () => {
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
