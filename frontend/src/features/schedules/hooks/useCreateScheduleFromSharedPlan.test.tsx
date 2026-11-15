import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { ApiClientProvider, useMutationFeedbackState } from "@/app/ApiClientProvider";
import { MutationController } from "@/domain/concurrency/MutationController";
import { ReadController } from "@/domain/concurrency/ReadController";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { ScheduleState } from "@generated/enums";
import type { ScheduleDefinition, SharedPlanScheduleCreateRequest } from "@generated/models";
import { decodeResponse, operations } from "@generated/operations";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import { fixtureRepositoryInstanceId, PRIMARY_REPOSITORY } from "../../../../e2e/fixtures/identities";
import { FIXTURE_PLANS } from "../../../../e2e/fixtures/plans";
import { fixtureScheduleDefinition } from "../../../../e2e/fixtures/schedules";
import { useCreateScheduleFromSharedPlan } from "./useCreateScheduleFromSharedPlan";

describe("Schedule creation response authority", () => {
  it.each([false, true])("retains commit causality when the returned digest mismatch is %s", async (mismatch) => {
    const target = FIXTURE_PLANS.target("python-quality");
    const repositoryId = PRIMARY_REPOSITORY.repository_id;
    const request: SharedPlanScheduleCreateRequest = {
      name: "Nightly quality",
      repository_ids: [repositoryId],
      expected_repository_instance_ids: { [repositoryId]: fixtureRepositoryInstanceId(repositoryId) },
      plan_key: "python-quality",
      expected_plan_revision: FIXTURE_PLANS.document("python-quality").sha256,
      expected_plan_id: target.plan.plan_id,
      expected_execution_digest: target.identity.digest,
      inputs: {},
      interval_minutes: 60,
      state: ScheduleState.ACTIVE,
    };
    const definition = fixtureScheduleDefinition(
      "nightly", request.name, request.repository_ids, request.interval_minutes, request.state,
      mismatch ? { ...target, identity: { digest: `sha256:${"f".repeat(64)}` } } : target,
    );
    const response = decodeResponse(operations.create_schedule_from_shared_plan.operationId, definition);
    expect(response.target.identity.digest === request.expected_execution_digest).toBe(!mismatch);
    const mutations = new MutationController();
    const reads = new ReadController(mutations.causality);
    const invalidate = vi.spyOn(reads, "invalidateMany");
    const client = new InMemoryApiClient();
    vi.spyOn(client, "createScheduleFromSharedPlan").mockImplementation(async () => ({
      outcome: "saved",
      ...await mutations.execute("schedules", async () => response),
    }));
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ApiClientProvider client={client} mutationController={mutations} readController={reads}>
        {children}
      </ApiClientProvider>
    );
    const { result } = renderHook(() => ({
      command: useCreateScheduleFromSharedPlan(),
      feedback: useMutationFeedbackState(),
    }), { wrapper });
    let outcome: WriteCommandResult<ScheduleDefinition> | undefined;
    await act(async () => { outcome = await result.current.command.create("nightly", request); });

    expect(outcome).toMatchObject(mismatch
      ? { outcome: "write-error", recovery: "refresh" }
      : { outcome: "saved", value: response, causalityRevision: 1 });
    expect(result.current.feedback.result).toEqual(outcome);
    expect(mutations.causality.current()).toBe(1);
    expect(invalidate.mock.calls.some(([, revision]) => revision === 1)).toBe(true);
  });
});
