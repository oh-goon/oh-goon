import { fireEvent, render, screen, within } from "@testing-library/react";

import { GameTapeSummary } from "@/features/game-tape/components/GameTapeSummary";
import type { AgentNoteReceipt, ParallelAgentTape } from "@generated/models";
import { PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";
import { runDetail, runState } from "../../../../e2e/fixtures/runProjection";

describe("GameTapeSummary parallel agent work", () => {
  it("shows both Run states and attributed note metadata without showing note bodies", () => {
    const privateBody = "Private candidate reasoning omitted from the Game Tape";
    const publication = {
      note_id: 1,
      kind: "idea",
      request_id: "append-1",
      author: {
        fleet_run_id: "agent-run-a",
        attempt_id: "attempt-a",
        step_id: "agent-step",
        repository_id: PRIMARY_REPOSITORY.repository_id,
        workspace_id: "workspace-a",
        base_source_sha256: "a".repeat(64),
      },
      body: privateBody,
    } satisfies AgentNoteReceipt & { body: string };
    const parallelWork: ParallelAgentTape = {
      work: {
        intent: {
          work_id: "parallel-task",
          base_revision: "b".repeat(40),
          repository_id: PRIMARY_REPOSITORY.repository_id,
          source: {
            authority_id: "source-authority",
            subject_instance_id: "repository-subject",
            source_snapshot: { sha256: publication.author.base_source_sha256, byte_count: 12, entry_count: 1 },
          },
          verifier_id: "frozen-verifier",
          notes_enabled: true,
          children: [
            { fleet_run_id: "agent-run-a", native_selection_id: "native-a", task_sha256: "c".repeat(64) },
            { fleet_run_id: "agent-run-b", native_selection_id: "native-b", task_sha256: "d".repeat(64) },
          ],
        },
        children: [
          { fleet_run_id: "agent-run-a", dispatch: "admitted", status: "RUNNING" },
          { fleet_run_id: "agent-run-b", dispatch: "admitted", status: "CANCELLED" },
        ],
        dispatch_closed: true,
        cancelled: false,
      },
      publications: [publication],
      deliveries: [{
        note_id: publication.note_id,
        attempt_id: "attempt-b",
        receiving_run_id: "agent-run-b",
        step_id: "agent-step",
        recipient: "admitted-recipient",
        model: "admitted-model",
        outcome: "acknowledged",
      }],
    };
    const detail = runDetail(runState("agent-run-a", "RUNNING", [PRIMARY_REPOSITORY], 1, 1), "RUNNING", false);
    render(<GameTapeSummary tape={{ ...detail.game_tape, parallel_agent_work: parallelWork }} />);

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
