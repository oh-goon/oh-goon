import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { GameTape } from "@generated/models";
import { GameTapeOutputs } from "@/features/game-tape/components/GameTapeOutputs";

describe("GameTapeOutputs", () => {
  it("renders backend-projected artifact and published output evidence", () => {
    const tape = {
      repositories: [{
        repository_execution_id: "repo-exec-1",
        repository: { repository_id: "repo-1", name: "api", path: "/repo" },
        status: "PASSED",
        fingerprint: { managed_input_count: 0, content_input_count: 0, tools: [], runtime_facts: [], host_environment_names: [] },
        steps: [{
          step_execution_id: "step-exec-1", step_id: "build", kind: "command", order: 1, status: "PASSED",
          command: [], working_directory: ".", environment: [], attempts: [], attempt_transitions: [], output_names: ["wheel"],
          artifacts: [{ status: "PRESENT", path: "dist/app.whl", sha256: "2".repeat(64), byte_count: 42 }],
          outputs: [{ name: "wheel", sha256: "3".repeat(64), byte_count: 42 }],
          why: { declaration: "plan", reached: true, explanation: "ran" },
        }],
      }],
    } as unknown as GameTape;
    render(<GameTapeOutputs tape={tape} />);
    expect(screen.getByRole("heading", { name: "Outputs" })).toBeTruthy();
    expect(screen.getAllByText("dist/app.whl")).toHaveLength(2);
    expect(screen.getByText("wheel")).toBeTruthy();
  });
});
