import { fireEvent, render, screen, within } from "@testing-library/react";

import type { AgentCampaignReadback, AgentCampaignRecord } from "@generated/models";
import { GameTapeCampaignJournal } from "@/features/game-tape/components/GameTapeCampaignJournal";

function decision(revision: number, terminal = false): AgentCampaignRecord {
  return {
    session_id: `campaign:${revision}`,
    revision,
    fact: {
      kind: "decision", campaign_id: "campaign-original", expected_revision: revision - 1,
      decision_id: `decision-${revision}`, phase_ordinal: revision, remaining_runs: 0,
      consumed_runs: [{ fleet_run_id: "verification-run", terminal_sequence: 129, operation_ids: [] }],
      stop_reason: terminal ? "converged" : null, next_phase: terminal ? null : "review",
      unresolved_run_ids: [], message: "Recorded policy message",
    },
  };
}

function readback(records: AgentCampaignRecord[], complete = true): AgentCampaignReadback {
  return { campaign_id: "campaign-original", journal_revision: 140, records, complete };
}

describe("GameTapeCampaignJournal", () => {
  it("shows committed decisions from the identified Journal cut", () => {
    render(<GameTapeCampaignJournal campaign={readback([decision(2, true)])} />);
    expect(screen.getAllByText("converged").some((node) => !node.closest("details"))).toBe(true);
    expect(screen.getByText("campaign-original")).toBeVisible();
    expect(screen.getByText("140")).toBeVisible();
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.getByText("decision-2")).toBeVisible();
    expect(screen.getByText("verification-run")).toBeVisible();
    expect(screen.getByText(/Terminal Journal sequence 129/)).toBeVisible();
    expect(screen.queryByRole("region", { name: "Selected work not admitted" })).toBeNull();
  });

  it("does not infer completion when the terminal decision is missing", () => {
    render(<GameTapeCampaignJournal campaign={readback([decision(2)])} />);
    expect(screen.getByText("Terminal decision unavailable")).toBeVisible();
    expect(screen.getByText(/Child Run success does not establish campaign completion/)).toBeVisible();
    expect(screen.queryByText("converged")).toBeNull();
  });

  it("shows the policy-selected accepted native test occurrence", () => {
    const record = decision(2, true);
    if (record.fact.kind !== "decision") throw new Error("Expected decision fixture");
    record.fact.stop_reason = "test_passed";
    record.fact.passed_test_step_id = "original-native-retest";
    render(<GameTapeCampaignJournal campaign={readback([record])} />);
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.getByText("Accepted native test Step")).toBeVisible();
    expect(screen.getByText("original-native-retest")).toBeVisible();
    expect(screen.getAllByText("test_passed").some((node) => !node.closest("details"))).toBe(true);
  });

  it("preserves the original candidate, budget and exact admitted phase membership", () => {
    render(<GameTapeCampaignJournal campaign={readback([
      { session_id: "intent", revision: 1, fact: {
        kind: "intent", campaign_id: "campaign-original", repository_id: "repository-original",
        original_execution_digest: "execution-original", maximum_runs: 8,
        initial_phase: "review", initial_step_ids: ["review-a", "review-b"],
        budget: { max_attempts: 2, timeout_seconds: 120, step_timeout_seconds: 30 },
        deadline_at: "2026-10-25T10:00:00Z", candidate_identity: "candidate-original", scope_identity: "scope-original",
      } },
      { session_id: "phase", revision: 2, fact: {
        kind: "phase", campaign_id: "campaign-original", expected_revision: 1,
        fleet_run_id: "admitted-original", execution_digest: "phase-execution", selected_step_ids: ["review-a", "review-b"],
        context: { repair_id: "campaign-original", attempt: 1, max_attempts: 8, limits: { deadline_at: "2026-10-25T10:00:00Z", step_timeout_seconds: 30 } },
      } },
    ])} />);
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.getByText("candidate-original")).toBeVisible();
    expect(screen.getByText("execution-original")).toBeVisible();
    expect(screen.getByText("admitted-original")).toBeVisible();
    expect(screen.getAllByText("review-a")).toHaveLength(2);
    expect(screen.getAllByText("review-b")).toHaveLength(2);
    expect(screen.getByText("Initial selected phase")).toBeVisible();
    expect(screen.getByText("8")).toBeVisible();
    expect(screen.getByText("Terminal decision unavailable")).toBeVisible();
  });

  it("makes incomplete readback explicit and keeps all records reachable in bounded pages", () => {
    render(<GameTapeCampaignJournal campaign={readback(Array.from({ length: 31 }, (_, index) => decision(index + 2)), false)} />);
    expect(screen.getByRole("status")).toHaveTextContent("This Journal view is incomplete");
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.queryByText("decision-32")).toBeNull();
    fireEvent.click(within(screen.getByRole("navigation", { name: "Campaign Journal pages" })).getByRole("button", { name: "Next" }));
    expect(screen.getByText("decision-32")).toBeVisible();
    expect(screen.queryByText("decision-2")).toBeNull();
  });

  it("does not create campaign facts for an ordinary Run", () => {
    const { container } = render(<GameTapeCampaignJournal />);
    expect(container).toBeEmptyDOMElement();
  });

  it("preserves a recorded decision in an incomplete export without confirming campaign completion", () => {
    render(<GameTapeCampaignJournal campaign={readback([decision(2, true)], false)} />);
    expect(screen.getByText("Terminal decision unavailable")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("This Journal view is incomplete");
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.getByText("converged")).toBeVisible();
  });

  it("shows selected successors separately from unadmitted work without inventing an admitted Run", () => {
    const advance = decision(2);
    const stopped = decision(3);
    if (advance.fact.kind !== "decision" || stopped.fact.kind !== "decision") throw new Error("Expected decision fixtures");
    advance.fact.next_step_ids = ["selected-next-a", "selected-next-b"];
    stopped.fact.next_phase = null;
    stopped.fact.stop_reason = "execution_unresolved";
    stopped.fact.consumed_runs = [];
    stopped.fact.phase_ordinal = 0;
    stopped.fact.remaining_runs = 8;
    stopped.fact.unstarted_selection = {
      selected_run_id: "selected-only-run", phase_ordinal: 1, phase: "review",
      selected_step_ids: ["selected-next-a", "selected-next-b"], admission_status: "not_admitted",
    };
    const { rerender } = render(<GameTapeCampaignJournal campaign={readback([advance])} />);
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.getByText("Next selected Steps")).toBeVisible();
    rerender(<GameTapeCampaignJournal campaign={readback([stopped])} />);
    const unstarted = screen.getByRole("region", { name: "Selected work not admitted" });
    expect(within(unstarted).getByText("selected-only-run")).toBeVisible();
    expect(within(unstarted).getByText("not_admitted")).toBeVisible();
    expect(within(unstarted).getByText("review · 1")).toBeVisible();
    expect(within(unstarted).getByText("selected-next-a")).toBeVisible();
    expect(within(unstarted).queryByText("Admitted Run")).toBeNull();
    expect(within(unstarted).queryByRole("link")).toBeNull();
    expect(screen.queryByText("verification-run")).toBeNull();
  });

  it("keeps large exact next-Step membership paged with its retained count", () => {
    const record = decision(2);
    if (record.fact.kind !== "decision") throw new Error("Expected decision fixture");
    record.fact.next_step_ids = Array.from({ length: 31 }, (_, index) => `next-step-${index}`);
    render(<GameTapeCampaignJournal campaign={readback([record])} />);
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(screen.getByText("Retained Steps: 31")).toBeVisible();
    expect(screen.queryByText("next-step-30")).toBeNull();
    fireEvent.click(within(screen.getByRole("navigation", { name: "Next selected Steps pages" })).getByRole("button", { name: "Next" }));
    expect(screen.getByText("next-step-30")).toBeVisible();
    expect(screen.queryByText("next-step-0")).toBeNull();
  });
});
