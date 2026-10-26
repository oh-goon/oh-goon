import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientProvider, useApiClient, useMutationBusy } from "@/app/ApiClientProvider";
import type { ApiClient } from "@/domain/api/ApiClient";
import { MutationController } from "@/domain/concurrency/MutationController";
import { ReadController } from "@/domain/concurrency/ReadController";
import { CurrentRunPanel } from "@/features/maintenance/components/CurrentRunPanel";
import { FleetRunStatus } from "@generated/enums";
import { operations, operationUrl } from "@generated/operations";
import { fixtureUnadmittedCampaignResult } from "../../../../e2e/fixtures/gameTape";
import { PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";
import { runState } from "../../../../e2e/fixtures/runProjection";

const repairRequest: Parameters<ApiClient["repairSharedPlan"]>[0] = {
  fleet_run_id: "reviewed-campaign", plan_key: "reviewed-plan",
  expected_plan_id: "reviewed-plan", expected_plan_revision: "a".repeat(64),
  expected_execution_digest: `sha256:${"b".repeat(64)}`,
  repository_ids: [PRIMARY_REPOSITORY.repository_id],
  repository_instance_ids: { [PRIMARY_REPOSITORY.repository_id]: "instance" }, inputs: {},
  agent: { model_profile: "review", acceptance: "review", repository_root: "/repo", skill_root: "/skills" },
  repair: { review_passes: 2, write_roots: ["src"], timeout_seconds: 600, step_timeout_seconds: 120 },
  authorize_repair: true,
};
const activeRun = runState("admitted-review-phase", FleetRunStatus.RUNNING, [PRIMARY_REPOSITORY], 1, 0);
const cancelledRun = runState(activeRun.fleet_run_id, FleetRunStatus.CANCELLING, [PRIMARY_REPOSITORY], 1, 0);

afterEach(() => { vi.unstubAllGlobals(); });

describe("repair cancellation transport", () => {
  it.each(["cancel-first", "repair-first"] as const)("keeps live Run cancellation available and preserves both writes when %s", async (order) => {
    const responses: Array<(response: Response) => void> = [];
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => { responses.push(resolve); }));
    vi.stubGlobal("fetch", fetchMock);
    const mutations = new MutationController();
    const reads = new ReadController(mutations.causality);
    let client!: ApiClient;
    const commits: number[] = [];
    mutations.subscribeCommits((_scope, revision) => { commits.push(revision); });
    let cancellation: ReturnType<ApiClient["cancelFleetRun"]> | undefined;
    function LiveRun() {
      const api = useApiClient();
      client = api;
      const busy = useMutationBusy();
      return <>
        <button disabled={busy}>Ordinary write</button>
        <CurrentRunPanel run={activeRun} detail={null} mode="active" onInspect={() => undefined}
          onCancel={(runId) => { cancellation = api.cancelFleetRun(runId); }} />
      </>;
    }
    render(<ApiClientProvider mutationController={mutations} readController={reads}><LiveRun /></ApiClientProvider>);
    let repair!: ReturnType<ApiClient["repairSharedPlan"]>;
    act(() => { repair = client.repairSharedPlan(repairRequest); });
    const payloads = [fixtureUnadmittedCampaignResult(repairRequest.fleet_run_id), cancelledRun];
    try {
      expect(screen.getByRole("button", { name: "Ordinary write" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Cancel run" })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: "Cancel run" }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm cancel" }));
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(fetchMock.mock.calls[1]).toEqual([
        `/api${operationUrl(operations.cancel_fleet_run.operationId, { path: { fleet_run_id: activeRun.fleet_run_id } })}`,
        expect.objectContaining({ method: operations.cancel_fleet_run.method }),
      ]);

      const completions = order === "cancel-first" ? [1, 0] : [0, 1];
      for (const [position, index] of completions.entries()) {
        let finishRead!: (value: string) => void;
        const staleRead = reads.execute("fleet-run-detail", null, () => new Promise<string>((resolve) => { finishRead = resolve; }));
        await act(async () => {
          responses[index]!(Response.json(payloads[index]));
          await expect(index === 0 ? repair : cancellation).resolves.toMatchObject({ outcome: "saved", causalityRevision: position + 1 });
        });
        finishRead("old Run snapshot");
        await expect(staleRead).resolves.toEqual({ outcome: "superseded" });
        expect(mutations.getSnapshot()).toBe(position === 0);
        if (position === 0) {
          expect(screen.getByRole("button", { name: "Ordinary write" })).toBeDisabled();
          await expect(client.includeRepository("other-repository", "other-instance")).resolves.toMatchObject({ outcome: "write-error", recovery: "retry" });
          expect(fetchMock).toHaveBeenCalledTimes(2);
        }
      }
      expect(commits).toEqual([1, 2]);
      expect(screen.getByRole("button", { name: "Ordinary write" })).toBeEnabled();
    } finally {
      await act(async () => {
        responses.forEach((respond, index) => { respond(Response.json(payloads[index])); });
        await Promise.allSettled([repair, cancellation]);
      });
    }
  });
});
