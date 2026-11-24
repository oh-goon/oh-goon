import { fireEvent, render, screen, within } from "@testing-library/react";

import { GameTapeSummary } from "@/features/game-tape/components/GameTapeSummary";
import { PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";
import { fixtureParallelAgentWork } from "../../../../e2e/fixtures/parallelAgentWork";
import { runDetail, runState } from "../../../../e2e/fixtures/runProjection";

describe("GameTapeSummary parallel agent work", () => {
  it("shows both Run states and attributed note metadata without showing note bodies", () => {
    const privateBody = "Private candidate reasoning omitted from the Game Tape";
    const parallelWork = fixtureParallelAgentWork("agent-run-a", "RUNNING", "agent-run-b", "CANCELLED");
    const [publication] = parallelWork.publications ?? [];
    if (!publication) throw new Error("Expected one note publication");
    const privatePublication = { ...publication, body: privateBody };
    const detail = runDetail(runState("agent-run-a", "RUNNING", [PRIMARY_REPOSITORY], 1, 1), "RUNNING", false);
    render(<GameTapeSummary tape={{
      ...detail.game_tape,
      parallel_agent_work: { ...parallelWork, publications: [privatePublication] },
    }} />);

    const disclosure = screen.getByText("Parallel agent work");
    fireEvent.click(disclosure);
    const panel = disclosure.closest("details");
    if (!panel) throw new Error("Expected parallel agent work disclosure");
    const section = within(panel);
    expect(section.getByText("parallel-task")).toBeVisible();
    expect(section.getByRole("heading", { name: "Published notes (1)" })).toBeVisible();
    expect(section.getByRole("heading", { name: "Note deliveries (1)" })).toBeVisible();
    const [firstRun, secondRun, published, delivery] = section.getAllByRole("listitem");
    expect(firstRun).toHaveTextContent("agent-run-a · RUNNING");
    expect(secondRun).toHaveTextContent("agent-run-b · CANCELLED");
    expect(published).toHaveTextContent("#1 · idea · agent-run-a");
    expect(delivery).toHaveTextContent("#1 · agent-run-b · Acknowledged");
    expect(screen.queryByText(privateBody)).not.toBeInTheDocument();
  });
});
