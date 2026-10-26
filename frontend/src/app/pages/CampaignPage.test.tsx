import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { CampaignPage } from "@/app/pages/CampaignPage";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { fixtureUnadmittedCampaignResult } from "../../../e2e/fixtures/gameTape";

class CampaignClient extends InMemoryApiClient {
  readonly reads: string[] = [];
  constructor(private readonly returnedId = "campaign-browser-refused") { super(); }
  override async agentCampaign(campaignId: string) {
    this.reads.push(campaignId);
    return { ...fixtureUnadmittedCampaignResult(), campaign_id: this.returnedId };
  }
}

beforeEach(() => { window.location.hash = "#runs?campaign=campaign-browser-refused"; });

describe("Campaign browser readback", () => {
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
});
