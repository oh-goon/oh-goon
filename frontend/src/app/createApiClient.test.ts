import { beforeEach, describe, expect, it, vi } from "vitest";

import { createApiClient } from "@/app/createApiClient";
import { readOperation } from "@/app/api/httpTransport";
import { writeOperation } from "@/app/api/mutationTransport";
import { MutationController } from "@/domain/concurrency/MutationController";
import { operations as op } from "@generated/operations";

vi.mock("@/app/api/mutationTransport", () => ({ writeOperation: vi.fn() }));
vi.mock("@/app/api/httpTransport", () => ({ readOperation: vi.fn() }));

const write = vi.mocked(writeOperation);
const read = vi.mocked(readOperation);

describe("createApiClient write fence transport", () => {
  beforeEach(() => {
    write.mockReset();
    read.mockReset();
    write.mockResolvedValue({ outcome: "saved", value: undefined, causalityRevision: 1 } as never);
  });

  it("requests artifact byte observation only through an explicit read selection", () => {
    const client = createApiClient(new MutationController());
    const options = { signal: new AbortController().signal };
    void client.fleetRunDetail("recorded-run", options);
    void client.fleetRunDetail("recorded-run", options, false);
    void client.fleetRunDetail("recorded-run", options, true);
    expect(read).toHaveBeenNthCalledWith(1, op.get_fleet_run.operationId, { path: { fleet_run_id: "recorded-run" } }, options);
    expect(read).toHaveBeenNthCalledWith(2, op.get_fleet_run.operationId, { path: { fleet_run_id: "recorded-run" } }, options);
    expect(read).toHaveBeenNthCalledWith(3, op.get_fleet_run.operationId, { path: { fleet_run_id: "recorded-run" }, query: { observe_artifacts: true } }, options);
    expect(write).not.toHaveBeenCalled();
  });

  it("transports reviewed Agent selection and nullable caller inputs unchanged", () => {
    const mutations = new MutationController();
    const client = createApiClient(mutations);
    const agent = { implementation: "codex" as const, profile: "native-review", model_profile: null, acceptance: "agentic-review", repository_root: "/work/repos", skill_root: "/work/workflow" };
    void client.startSharedPlanRun("agent-run", "selected", ["repo-a"], "reviewed-plan", "a".repeat(64), `sha256:${"b".repeat(64)}`, { "repo-a": "instance-a" }, { optional: null, explicit: "" }, agent);
    expect(write).toHaveBeenCalledWith(mutations, "fleet-run", op.run_shared_plan.operationId, { body: {
      fleet_run_id: "agent-run", plan_key: "selected", expected_plan_id: "reviewed-plan",
      expected_plan_revision: "a".repeat(64), expected_execution_digest: `sha256:${"b".repeat(64)}`,
      repository_ids: ["repo-a"], repository_instance_ids: { "repo-a": "instance-a" }, inputs: { optional: null, explicit: "" }, agent,
    } });
  });

  it("transports reviewed mutation fences without renaming or dropping them", () => {
    const mutations = new MutationController();
    const client = createApiClient(mutations);

    void client.excludeRepository("repo-a", "instance-a", "maintenance");
    void client.includeRepository("repo-a", "instance-a");
    void client.cancelQueuedAdmission("run-a", 17);
    void client.revokeExecutorTrust("executor-a", 4, 9);
    void client.removeLocation("/work", "a".repeat(64));
    void client.acknowledgeEffect("effect-a", "reviewed", 21);
    void client.cancelFleetRun("run-b");

    expect(write).toHaveBeenNthCalledWith(1, mutations, "repositories", op.exclude_repository.operationId, {
      body: { repository: "repo-a", expected_repository_instance_id: "instance-a", reason: "maintenance" },
    });
    expect(write).toHaveBeenNthCalledWith(2, mutations, "repositories", op.include_repository.operationId, {
      body: { repository: "repo-a", expected_repository_instance_id: "instance-a" },
    });
    expect(write).toHaveBeenNthCalledWith(3, mutations, "fleet-run", op.cancel_queued_admission.operationId, {
      path: { fleet_run_id: "run-a" }, body: { expected_sequence: 17 },
    });
    expect(write).toHaveBeenNthCalledWith(4, mutations, "fleet-run", op.revoke_executor_trust.operationId, {
      path: { executor_id: "executor-a" },
      body: { expected_generation: 4, expected_operational_revision: 9 },
    });
    expect(write).toHaveBeenNthCalledWith(5, mutations, "locations", op.remove_location.operationId, {
      body: { path: "/work", expected_revision: "a".repeat(64) },
    });
    expect(write).toHaveBeenNthCalledWith(6, mutations, "recovery", op.acknowledge_effect.operationId, {
      path: { operation_id: "effect-a" }, body: { note: "reviewed", expected_record_version: 21 },
    });
    expect(write).toHaveBeenNthCalledWith(7, mutations, "fleet-run", op.cancel_fleet_run.operationId, {
      path: { fleet_run_id: "run-b" },
    }, { dispatch: "fleet-run-cancel" });
  });
});
