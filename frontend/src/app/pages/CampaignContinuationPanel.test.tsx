import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { writeAppLocation } from "@/app/navigation";
import { CampaignContinuationPanel } from "@/app/pages/CampaignContinuationPanel";
import type { ApiClient } from "@/domain/api/ApiClient";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { CONTINUATION_CAMPAIGN_ID, fixtureAgentContinuation, fixtureContinuationReceipt } from "../../../e2e/fixtures/agentContinuation";
import { PRIMARY_REPOSITORY } from "../../../e2e/fixtures/identities";
import { FIXTURE_FLEET_PLAN_TARGET } from "../../../e2e/fixtures/plans";
import { runDetail, runState } from "../../../e2e/fixtures/runProjection";
import { FleetRunStatus, GameTapeStatus } from "@generated/enums";
import type { AgentContinuationInspection } from "@generated/models";

class ContinuationClient extends InMemoryApiClient {
  readonly inspections: { campaignId: string; implementation?: string | null; profile?: string | null }[] = [];
  readonly submissions: Parameters<ApiClient["continueAgentCampaign"]>[1][] = [];
  readonly outcomes: string[] = [];
  inspection = fixtureAgentContinuation();
  failOutcome = false;
  settle: ((result: Awaited<ReturnType<ApiClient["continueAgentCampaign"]>>) => void) | null = null;
  override async inspectAgentContinuation(campaignId: string, implementation?: Parameters<ApiClient["inspectAgentContinuation"]>[1], profile?: string | null) {
    this.inspections.push({ campaignId, implementation, profile });
    return this.inspection;
  }
  override continueAgentCampaign(_campaignId: string, request: Parameters<ApiClient["continueAgentCampaign"]>[1]): ReturnType<ApiClient["continueAgentCampaign"]> {
    this.submissions.push(request);
    return new Promise((resolve) => { this.settle = resolve; });
  }
  override async fleetRunDetail(id: string) {
    this.outcomes.push(id);
    if (this.failOutcome) throw new Error("Run not found; original request could still be pending");
    const request = this.submissions.find((item) => item.new_fleet_run_id === id);
    if (!request) throw new Error("No fixture receipt");
    const target = { ...FIXTURE_FLEET_PLAN_TARGET, agent_continuation: fixtureContinuationReceipt(request) };
    const detail = runDetail(runState(id, FleetRunStatus.COMPLETED, [PRIMARY_REPOSITORY], 1, 1, target), GameTapeStatus.PASSED, false);
    detail.feedback = { ...fixtureAgentContinuation().feedback, fleet_run_id: id, continuation_state: "native_finished" };
    return detail;
  }
}

beforeEach(() => { window.location.hash = `#runs?campaign=${CONTINUATION_CAMPAIGN_ID}`; });

function mount(client = new ContinuationClient()) {
  const view = render(<ApiClientProvider client={client}><CampaignContinuationPanel campaignId={CONTINUATION_CAMPAIGN_ID} /></ApiClientProvider>);
  return { client, view };
}

async function inspect() {
  fireEvent.click(screen.getByRole("button", { name: "Check current candidate" }));
  const inspection = await screen.findByRole("region", { name: "Current continuation inspection" });
  await waitFor(() => expect(within(inspection).getByRole("heading", { name: "Current continuation inspection" })).toHaveFocus());
  return inspection;
}

async function authorize() {
  await inspect();
  fireEvent.change(screen.getByLabelText("New feedback or clarification"), { target: { value: "Fix the regression while preserving the original verification scope." } });
  fireEvent.click(screen.getByRole("checkbox"));
}

describe("Fresh campaign continuation", () => {
  it("checks the current registered source explicitly, shows bounded evidence and revokes authorization after edits or another check", async () => {
    const { client } = mount();
    expect(client.inspections).toEqual([]);
    const inspection = await inspect();
    for (const value of within(inspection).getAllByText("captured-candidate")) expect(value).toBeVisible();
    expect(within(inspection).getByText("c".repeat(64))).toBeVisible();
    fireEvent.click(within(inspection).getByText("Current candidate diff"));
    expect(within(inspection).getByText("This diff is truncated. Omitted changes are not shown.")).toBeVisible();
    expect(within(inspection).getByText(/return current_result/)).toBeVisible();
    expect(within(inspection).getByText("Original executable modes were not retained; mode lines do not establish historical permission changes.")).toBeVisible();
    expect(within(inspection).getByRole("link").getAttribute("href")).toContain("run=fleet-run-browser-fixture-failed");
    fireEvent.change(screen.getByLabelText("New feedback or clarification"), { target: { value: "Clarify the failing case." } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Start fresh continuation Run" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("New feedback or clarification"), { target: { value: "Clarify the second failing case." } });
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    fireEvent.click(screen.getByRole("checkbox"));
    await inspect();
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    fireEvent.change(screen.getByLabelText("Current native Agent profile"), { target: { value: "new-current-profile" } });
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(client.submissions).toEqual([]);
  });

  it("proposes one new Run before dispatch and reconciles a lost acknowledgement by reading that same identity without resubmission", async () => {
    const { client } = mount();
    await authorize();
    const submit = screen.getByRole("button", { name: "Start fresh continuation Run" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(client.submissions).toHaveLength(1);
    const request = client.submissions[0]!;
    expect(request).toMatchObject({ review: client.inspection.review, authorize: true });
    expect(window.location.hash).toContain(`continuation_run=${request.new_fleet_run_id}`);
    expect(screen.getByText(request.new_fleet_run_id)).toBeVisible();
    expect(client.outcomes).toEqual([]);
    await act(async () => client.settle!({ outcome: "write-error", diagnosticId: "lost-ack", recovery: "refresh" }));
    expect(submit).toBeDisabled();
    expect(screen.getByText(/Acknowledgement is unavailable/)).toBeVisible();
    client.failOutcome = true;
    fireEvent.click(screen.getByRole("button", { name: "Check this Run outcome" }));
    await screen.findByRole("alert");
    expect(client.outcomes).toEqual([request.new_fleet_run_id]);
    expect(submit).toBeDisabled();
    client.failOutcome = false;
    fireEvent.click(screen.getByRole("button", { name: "Check this Run outcome" }));
    await screen.findByText("This fresh Run was admitted. Inspect its Game Tape for execution and verification results.");
    expect(screen.getByText("Native Agent finished; verification is separate")).toHaveAttribute("role", "status");
    expect(screen.queryByText("Verified success")).toBeNull();
    expect(client.outcomes).toEqual([request.new_fleet_run_id, request.new_fleet_run_id]);
    expect(client.submissions).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
  });

  it.each(["stale", "blocked", "wrong-receipt"])("keeps %s locked to the proposed identity until canonical Run readback", async (mode) => {
    const { client } = mount();
    await authorize();
    fireEvent.click(screen.getByRole("button", { name: "Start fresh continuation Run" }));
    const request = client.submissions[0]!;
    await act(async () => client.settle!(mode === "stale"
      ? { outcome: "stale", diagnosticId: "source-changed" }
      : mode === "blocked" ? { outcome: "write-error", diagnosticId: "applied-but-malformed-receipt", recovery: "blocked" }
      : { outcome: "saved", causalityRevision: 1, value: { fleet_run_id: "different-run", acknowledgment: "admitted", receipt: fixtureContinuationReceipt(request) } }));
    expect(screen.queryByText("This fresh Run was admitted. Inspect its Game Tape for execution and verification results.")).toBeNull();
    expect(screen.getByRole("button", { name: "Start fresh continuation Run" })).toBeDisabled();
    expect(client.submissions).toHaveLength(1);
    await inspect();
    expect(window.location.hash).toContain(`continuation_run=${request.new_fleet_run_id}`);
    expect(screen.getByRole("button", { name: "Start fresh continuation Run" })).toBeDisabled();
    expect(client.submissions).toHaveLength(1);
  });

  it("ignores a late acknowledgement after the operator navigates away", async () => {
    const { client } = mount();
    await authorize();
    fireEvent.click(screen.getByRole("button", { name: "Start fresh continuation Run" }));
    const request = client.submissions[0]!;
    act(() => writeAppLocation("push", "runs", { campaign: "another-campaign" }));
    await act(async () => client.settle!({ outcome: "saved", causalityRevision: 1, value: { fleet_run_id: request.new_fleet_run_id, acknowledgment: "admitted", receipt: fixtureContinuationReceipt(request) } }));
    expect(window.location.hash).toBe("#runs?campaign=another-campaign");
    expect(screen.queryByText("This fresh Run was admitted. Inspect its Game Tape for execution and verification results.")).toBeNull();
  });

  it("restores only the proposed Run identity after reload and requires outcome readback", () => {
    window.location.hash += "&continuation_run=previous-request";
    const { client } = mount();
    expect(screen.getByText("previous-request")).toBeVisible();
    expect(screen.getByRole("button", { name: "Start fresh continuation Run" })).toBeDisabled();
    expect(screen.getByLabelText("New feedback or clarification")).toHaveValue("");
    expect(client.submissions).toEqual([]);
    expect(client.outcomes).toEqual([]);
  });

  it("keeps unavailable diff evidence and backend refusal visible without turning missing evidence into no changes", async () => {
    const client = new ContinuationClient();
    client.inspection = { ...client.inspection, can_continue: false, refusal_reasons: ["No current native qualification is available."],
      source: { ...client.inspection.source, diff: { availability: "unavailable", text: "", truncated: false, reason: "Native Git is unavailable.", baseline: "last_captured_candidate", byte_count: null, omitted_bytes: null } } };
    mount(client);
    const inspection = await inspect();
    fireEvent.click(within(inspection).getByText("Current candidate diff"));
    expect(within(inspection).getByText(/Native Git diff unavailable/)).toBeVisible();
    expect(within(inspection).getByText("No current native qualification is available.")).toBeVisible();
    expect(within(inspection).queryByText("Native Git reported no differences.")).toBeNull();
    fireEvent.change(screen.getByLabelText("New feedback or clarification"), { target: { value: "Clarification remains passive." } });
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start fresh continuation Run" })).toBeDisabled();
    expect(client.submissions).toEqual([]);
  });

  it.each(["awaiting_input", "stopped", "needs_review", "native_finished", "verified_success"] as const)("renders server-owned %s without upgrading native completion", async (state) => {
    const client = new ContinuationClient();
    client.inspection = { ...client.inspection, state };
    mount(client);
    const inspection = await inspect();
    expect(within(inspection).getByText({ awaiting_input: "Awaiting input", stopped: "Stopped", needs_review: "Needs review", native_finished: "Native Agent finished; verification is separate", verified_success: "Verified success" }[state])).toHaveAttribute("role", "status");
    expect(client.submissions).toEqual([]);
  });

  it("refuses cross-campaign inspection and enforces the feedback byte bound", async () => {
    const client = new ContinuationClient();
    client.inspection = { ...client.inspection, campaign_id: "another-campaign" } as AgentContinuationInspection;
    mount(client);
    fireEvent.click(screen.getByRole("button", { name: "Check current candidate" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("region", { name: "Current continuation inspection" })).toBeNull();
    client.inspection = fixtureAgentContinuation();
    await inspect();
    fireEvent.change(screen.getByLabelText("New feedback or clarification"), { target: { value: "🙂".repeat(2049) } });
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start fresh continuation Run" })).toBeDisabled();
  });
});
