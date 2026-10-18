import { render, screen } from "@testing-library/react";

import type { GameTape } from "@generated/models";
import { GameTapeAuthorityWhyNot } from "@/features/game-tape/components/GameTapeAuthorityWhyNot";

describe("GameTapeAuthorityWhyNot", () => {
  it("renders durable execution-authority refusal guidance without inventing authority", () => {
    const tape = {
      why_not: {
        code: "EXECUTION_AUTHORITY_DIVERGENCE",
        stage: "EXECUTION",
        retryable: false,
        authority_owner: "execution_authority",
        message: "runtime executable bytes diverged",
        safe_next_action: "Rerun from a fresh admission.",
      },
    } as unknown as GameTape;

    render(<GameTapeAuthorityWhyNot tape={tape} />);

    expect(screen.getByRole("heading", { name: "Why execution was refused" })).toBeTruthy();
    expect(screen.getByText("EXECUTION_AUTHORITY_DIVERGENCE")).toBeTruthy();
    expect(screen.getByText("runtime executable bytes diverged")).toBeTruthy();
    expect(screen.getByText("Rerun from a fresh admission.")).toBeTruthy();
  });
});
