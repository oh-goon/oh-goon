import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import type { ApiClient } from "@/domain/api/ApiClient";
import { SharedPlanPanel } from "@/features/league/components/SharedPlanPanel";
import { BrowserFixtureApiClient } from "../../../../e2e/BrowserFixtureApiClient";
import { LONG_REPOSITORY_NAME, fixtureRepositoryInstanceId } from "../../../../e2e/fixtures/identities";
import { FIXTURE_PLANS } from "../../../../e2e/fixtures/plans";
import { fixtureUnadmittedCampaignResult } from "../../../../e2e/fixtures/gameTape";

class RepairClient extends BrowserFixtureApiClient {
  readonly repairs: Parameters<ApiClient["repairSharedPlan"]>[0][] = [];
  settle: ((result: Awaited<ReturnType<ApiClient["repairSharedPlan"]>>) => void) | null = null;
  override repairSharedPlan(request: Parameters<ApiClient["repairSharedPlan"]>[0]): ReturnType<ApiClient["repairSharedPlan"]> {
    this.repairs.push(request);
    return new Promise((resolve) => { this.settle = resolve; });
  }
}

it.each(["uncertain", "unrecorded"])("binds repair authorization to the reviewed selection and preserves %s result authority", async (outcome) => {
  const client = new RepairClient();
  const ordinaryRun = vi.fn();
  const openCampaign = vi.fn();
  render(<ApiClientProvider client={client}><SharedPlanPanel league={await client.league()} onRun={ordinaryRun} onOpenCampaign={openCampaign} /></ApiClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("Saved Plan")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Saved Plan"), { target: { value: "python-quality" } });
  await waitFor(() => expect(screen.getByLabelText(LONG_REPOSITORY_NAME)).toBeEnabled());
  fireEvent.click(screen.getByLabelText(LONG_REPOSITORY_NAME));
  fireEvent.click(screen.getByText("Agent and bounded repair selection"));
  fireEvent.click(screen.getByLabelText("Select an Agent workflow"));
  fireEvent.change(screen.getByLabelText("Acceptance profile"), { target: { value: "agentic-review" } });
  fireEvent.change(screen.getByLabelText("Repository root (absolute)"), { target: { value: "/work/repos" } });
  fireEvent.change(screen.getByLabelText("Skill root (absolute)"), { target: { value: "/work/workflow" } });
  fireEvent.click(screen.getByLabelText("Run the complete bounded review-and-repair campaign"));
  fireEvent.change(screen.getByLabelText("Authorized write roots (one per line)"), { target: { value: "src\ntests" } });
  const authorization = screen.getByLabelText("Authorize this exact bounded repair submission");
  const submit = screen.getByRole("button", { name: "Start authorized repair campaign" });
  await waitFor(() => expect(authorization).toBeEnabled());
  expect(submit).toBeDisabled();
  fireEvent.click(authorization);
  expect(submit).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Campaign timeout (seconds)"), { target: { value: "300" } });
  expect(authorization).not.toBeChecked();
  expect(submit).toBeDisabled();
  fireEvent.click(authorization);
  fireEvent.click(submit);
  await waitFor(() => expect(client.repairs).toHaveLength(1));
  const request = client.repairs[0]!;
  const target = FIXTURE_PLANS.target("python-quality");
  expect(request).toMatchObject({
    plan_key: "python-quality", expected_plan_id: target.plan.plan_id,
    expected_plan_revision: "d".repeat(64), expected_execution_digest: target.identity.digest,
    repository_ids: ["repo-payments"], repository_instance_ids: { "repo-payments": fixtureRepositoryInstanceId("repo-payments") },
    inputs: {}, agent: { acceptance: "agentic-review", repository_root: "/work/repos", skill_root: "/work/workflow" },
    repair: { review_passes: 2, write_roots: ["src", "tests"], timeout_seconds: 300, step_timeout_seconds: 120 },
    authorize_repair: true,
  });
  expect(screen.getByText(request.fleet_run_id)).toBeVisible();
  expect(ordinaryRun).not.toHaveBeenCalled();
  if (outcome === "uncertain") {
    await act(async () => { client.settle!({ outcome: "write-error", diagnosticId: "transport-unavailable", recovery: "refresh" }); });
    expect(screen.getByText("The response is unavailable. Inspect this campaign ID for the original outcome before starting another invocation.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open campaign" }));
    expect(openCampaign).toHaveBeenCalledWith(request.fleet_run_id);
  } else {
    await act(async () => { client.settle!({ outcome: "saved", causalityRevision: 1, value: {
      ...fixtureUnadmittedCampaignResult(request.fleet_run_id), journal_revision: null, intent: null,
      complete: false, decision_history: [], unstarted_selection: null,
      controller_result: { availability: "unavailable" },
      invocation_result: { stop_reason: "timed_out", message: "", authority: "invocation-observation;not-a-committed-controller-decision",
        budget: { max_attempts: request.repair.review_passes, timeout_seconds: request.repair.timeout_seconds, step_timeout_seconds: request.repair.step_timeout_seconds } },
    } }); });
    expect(screen.getByText("No durable campaign intent is available for this result. Inspect the incomplete invocation result before starting another invocation.")).toBeVisible();
    expect(screen.getByText("This invocation stopped: timed_out")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Open campaign" })).toBeNull();
    expect(openCampaign).not.toHaveBeenCalled();
  }
  expect(client.repairs).toHaveLength(1);
  expect(authorization).not.toBeChecked();
});
