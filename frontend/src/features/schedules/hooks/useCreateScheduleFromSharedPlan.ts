import { useCallback } from "react";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import type { SharedPlanScheduleCreateRequest, ScheduleDefinition } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { ScheduleState } from "@generated/enums";

export interface CreateScheduleFromSharedPlanState {
  create(
    scheduleId: string,
    request: SharedPlanScheduleCreateRequest,
  ): Promise<WriteCommandResult<ScheduleDefinition>>;
}

export function useCreateScheduleFromSharedPlan(): CreateScheduleFromSharedPlanState {
  const client = useApiClient();
  const reportMutationFeedback = useReportMutationFeedback("schedules");
  const create = useCallback(async (
    scheduleId: string,
    request: SharedPlanScheduleCreateRequest,
  ) => {
    const result = validateCreatedScheduleIdentity(await client.createScheduleFromSharedPlan(scheduleId, request), scheduleId, request);
    reportMutationFeedback(result);
    return result;
  }, [client, reportMutationFeedback]);
  return { create };
}

function validateCreatedScheduleIdentity(
  result: WriteCommandResult<ScheduleDefinition>,
  expectedScheduleId: string,
  request: SharedPlanScheduleCreateRequest,
): WriteCommandResult<ScheduleDefinition> {
  if (result.outcome !== "saved") return result;
  const source = result.value.target.source;
  const responseAuthority = canonicalIdentity({
    repositoryIds: result.value.repository_ids,
    repositoryInstanceIds: result.value.repository_instance_ids,
    name: result.value.name,
    intervalMinutes: result.value.interval_minutes,
    state: result.value.state,
    sharedPlanKey: source.kind === "shared_plan" ? source.plan_key : null,
    sharedPlanRevision: source.kind === "shared_plan" ? source.sha256 : null,
    planId: result.value.target.kind === "fleet_plan" ? result.value.target.plan.plan_id : null,
  });
  const requestAuthority = canonicalIdentity({
    repositoryIds: request.repository_ids,
    repositoryInstanceIds: request.expected_repository_instance_ids,
    name: request.name,
    intervalMinutes: request.interval_minutes,
    state: request.state ?? ScheduleState.ACTIVE,
    sharedPlanKey: request.plan_key,
    sharedPlanRevision: request.expected_plan_revision,
    planId: request.expected_plan_id,
  });
  if (result.value.schedule_id === expectedScheduleId && responseAuthority === requestAuthority) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "schedule-create-identity",
    error: new Error(`Schedule create authority mismatch for ${expectedScheduleId}`),
    scope: "schedules",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
