import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { CampaignPage } from "@/app/pages/CampaignPage";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { fixtureUnadmittedCampaignResult } from "../../../e2e/fixtures/gameTape";
import { fixtureRunFeedback } from "../../../e2e/fixtures/runFeedback";
import type { AgentCampaignResult } from "@generated/models";

class CampaignClient extends InMemoryApiClient {
  readonly reads: string[] = [];
  constructor(private readonly returnedId = "campaign-browser-refused") { super(); }
  override async agentCampaign(campaignId: string) {
    this.reads.push(campaignId);
    return { ...fixtureUnadmittedCampaignResult(), campaign_id: this.returnedId };
  }
}

function campaignWithFeedback(campaignId: string, runId: string, candidate: string): AgentCampaignResult {
  return {
    schema: "oh-goon.agent-campaign-result/v2", campaign_id: campaignId, journal_revision: 203,
    result: "incomplete", complete: false, incomplete_reasons: ["Campaign terminal decision unavailable."],
    controller_result: { availability: "unavailable" }, intent: null,
    safe_next_action: "inspect_original_runs_and_resolve_ownership", unstarted_selection: null, decision_history: [],
    original_execution_digest: "execution-original", repositories: [], initial_candidate: null,
    last_admitted_candidate: candidate, final_candidate: null,
    runs: [{ fleet_run_id: runId, status: "FAILED", outcome: "FAILED" }],
    reviews: [], findings: [], adjudications: [], repair_attempts: [], verification: [],
    claim_authority: "Source-bound Agent claims retain their original authority.",
    continuation_authority: "A fresh authorized invocation is required.",
    feedback: fixtureRunFeedback(runId, { campaign: { campaign_id: campaignId, current_candidate_identity: candidate, scope_identity: "scope-original", original_execution_digest: "execution-original", phase: "regression", attempt: 2 } }),
  };
}

beforeEach(() => { window.location.hash = "#runs?campaign=campaign-browser-refused"; });

describe("Campaign browser readback", () => {
  it("presents the retained Run feedback through the existing campaign view", async () => {
    const client = new CampaignClient();
    client.agentCampaign = async () => campaignWithFeedback("campaign-browser-refused", "run-original", "candidate-original");
    render(<ApiClientProvider client={client}><CampaignPage /></ApiClientProvider>);
    const feedback = await screen.findByRole("region", { name: "Recorded attempt feedback" });
    expect(within(feedback).getByText("run-original")).toBeVisible();
    expect(within(feedback).getByText("candidate-original")).toBeVisible();
    expect(within(feedback).getByText("known_code_failure")).toBeVisible();
    expect(within(feedback).queryByRole("button")).toBeNull();
  });

  it("opens retained original decisions and selected work without a Run", async () => {
    const client = new CampaignClient();
    render(<ApiClientProvider client={client}><CampaignPage /></ApiClientProvider>);
    await waitFor(() => expect(screen.getByText("No Runs were admitted for this campaign.")).toBeVisible());
    expect(client.reads).toEqual(["campaign-browser-refused"]);
    expect(screen.getAllByText("Initial admission was refused before any Run started.").find((node) => !node.closest("details"))).toBeVisible();
    fireEvent.click(screen.getByText("Campaign intent, phase linkage and decisions"));
    expect(within(screen.getByRole("region", { name: "Selected work not admitted" })).getByText("refused-review-step")).toBeVisible();
    expect(screen.getByText("not_admitted")).toBeVisible();
  });

  it("refuses a read response for a different campaign", async () => {
    const client = new CampaignClient("different-campaign");
    render(<ApiClientProvider client={client}><CampaignPage /></ApiClientProvider>);
    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    expect(screen.queryByText("Initial admission was refused before any Run started.")).toBeNull();
  });

  it("accepts a campaign ID directly without first selecting a Run", () => {
    window.location.hash = "#runs?campaign=";
    const client = new CampaignClient();
    render(<ApiClientProvider client={client}><CampaignPage /></ApiClientProvider>);
    fireEvent.change(screen.getByLabelText("Campaign"), { target: { value: "campaign-browser-refused" } });
    fireEvent.click(screen.getByRole("button", { name: "Open campaign" }));
    expect(window.location.hash).toBe("#runs?campaign=campaign-browser-refused");
    expect(client.reads).toEqual([]);
  });

  it("does not publish late feedback after the selected campaign changes", async () => {
    const client = new CampaignClient();
    const pending = new Map<string, (value: AgentCampaignResult) => void>();
    client.agentCampaign = (campaignId: string) => new Promise<AgentCampaignResult>((resolve) => { pending.set(campaignId, resolve); });
    const view = render(<ApiClientProvider client={client}><CampaignPage key="original" /></ApiClientProvider>);
    await waitFor(() => expect(pending.has("campaign-browser-refused")).toBe(true));
    window.location.hash = "#runs?campaign=campaign-new";
    view.rerender(<ApiClientProvider client={client}><CampaignPage key="new" /></ApiClientProvider>);
    await waitFor(() => expect(pending.has("campaign-new")).toBe(true));
    await act(async () => pending.get("campaign-new")!(campaignWithFeedback("campaign-new", "run-new", "candidate-new")));
    await waitFor(() => expect(screen.getByText("candidate-new")).toBeVisible());
    await act(async () => pending.get("campaign-browser-refused")!(campaignWithFeedback("campaign-browser-refused", "run-old", "candidate-late")));
    expect(screen.queryByText("candidate-late")).toBeNull();
    expect(screen.getByText("candidate-new")).toBeVisible();
  });
});
