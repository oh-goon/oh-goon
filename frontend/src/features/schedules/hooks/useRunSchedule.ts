import { useCallback, useEffect, useRef } from "react";
import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import type { ScheduleCatalog, ScheduleOccurrence } from "@generated/models";
import { newScheduleRunId } from "@/domain/identity/commandIdentity";
import { PendingCommandIdentitySet } from "@/domain/concurrency/PendingCommandIdentitySet";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface RunScheduleState {
  run(id: string): Promise<WriteCommandResult<ScheduleOccurrence>>;
}

export function useRunSchedule(catalog: ScheduleCatalog | null): RunScheduleState {
  const client = useApiClient();
  // Starting one Schedule occurrence admits Fleet Runs, so the Fleet mutation
  // resource set is the canonical invalidation boundary. It already includes
  // schedule-state in addition to Run/Fleet/overview projections.
  const reportMutationFeedback = useReportMutationFeedback("fleet-run");
  const pendingRunIds = useRef(new PendingCommandIdentitySet(newScheduleRunId));
  useEffect(() => {
    pendingRunIds.current.observeDurableIdentities(
      (catalog?.schedules ?? []).flatMap((item) => item.latest_occurrence ? [item.latest_occurrence.schedule_run_id] : []),
    );
  }, [catalog]);
  const run = useCallback(async (id: string) => {
    const definition = (catalog?.schedules ?? []).find((item) => item.definition.schedule_id === id)?.definition;
    if (definition === undefined) {
      const diagnostic = appDiagnosticStore.record({
        source: "schedule-run-start-authority",
        error: new Error(`Schedule definition is unavailable for run command: ${id}`),
        scope: "fleet-run",
        causalityRevision: 0,
      });
      return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "blocked" } as const;
    }
    const binding = pendingRunIds.current.bindCommand(id, {
      generationId: definition.generation_id,
      recordVersion: definition.record_version,
    });
    const result = await client.startSchedule(
      id,
      binding.identity,
      binding.authority.generationId,
      binding.authority.recordVersion,
    );
    reportMutationFeedback(result);
    const accepted = validateScheduleRunIdentity(result, id, binding.identity);
    if (accepted !== result) reportMutationFeedback(accepted);
    pendingRunIds.current.settle(id, accepted);
    return accepted;
  }, [catalog, client, reportMutationFeedback]);
  return { run };
}

function validateScheduleRunIdentity(
  result: WriteCommandResult<ScheduleOccurrence>,
  expectedScheduleId: string,
  expectedScheduleRunId: string,
): WriteCommandResult<ScheduleOccurrence> {
  if (result.outcome !== "saved" || (
    result.value.schedule_id === expectedScheduleId
    && result.value.schedule_run_id === expectedScheduleRunId
  )) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "schedule-run-start-identity",
    error: new Error(`Schedule Run mutation identity mismatch: expected ${expectedScheduleId}/${expectedScheduleRunId}, received ${result.value.schedule_id}/${result.value.schedule_run_id}`),
    scope: "fleet-run",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
