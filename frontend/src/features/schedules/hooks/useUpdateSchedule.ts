import { useCallback } from "react";
import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import type { ScheduleDefinition, ScheduleReplace } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";

export interface UpdateScheduleState {
  update(id: string, schedule: ScheduleReplace): Promise<WriteCommandResult<ScheduleDefinition>>;
}

export function useUpdateSchedule(): UpdateScheduleState {
  const client = useApiClient();
  const reportMutationFeedback = useReportMutationFeedback("schedules");
  const update = useCallback(async (id: string, schedule: ScheduleReplace) => {
    const result = validateUpdatedScheduleIdentity(await client.updateSchedule(id, schedule), id, schedule);
    reportMutationFeedback(result);
    return result;
  }, [client, reportMutationFeedback]);
  return { update };
}

function validateUpdatedScheduleIdentity(
  result: WriteCommandResult<ScheduleDefinition>,
  expectedScheduleId: string,
  expected: ScheduleReplace,
): WriteCommandResult<ScheduleDefinition> {
  if (result.outcome !== "saved") return result;
  const responseAuthority = canonicalIdentity({
    generationId: result.value.generation_id,
    repositoryIds: result.value.repository_ids,
    repositoryInstanceIds: result.value.repository_instance_ids,
    name: result.value.name,
    target: result.value.target,
    intervalMinutes: result.value.interval_minutes,
    state: result.value.state,
  });
  const requestAuthority = canonicalIdentity({
    generationId: expected.generation_id,
    repositoryIds: expected.repository_ids,
    repositoryInstanceIds: expected.repository_instance_ids,
    name: expected.name,
    target: expected.target,
    intervalMinutes: expected.interval_minutes,
    state: expected.state,
  });
  if (
    result.value.schedule_id === expectedScheduleId
    && result.value.record_version > expected.record_version
    && responseAuthority === requestAuthority
  ) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "schedule-update-identity",
    error: new Error(`Schedule update authority mismatch for ${expectedScheduleId}/${expected.generation_id}@${expected.record_version}`),
    scope: "schedules",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
