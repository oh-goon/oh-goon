import { useCallback } from "react";
import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import type { ScheduleDefinition } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface DeleteScheduleState {
  remove(definition: ScheduleDefinition): Promise<WriteCommandResult<void>>;
}

export function useDeleteSchedule(): DeleteScheduleState {
  const client = useApiClient();
  const reportMutationFeedback = useReportMutationFeedback("schedules");
  const remove = useCallback(async (definition: ScheduleDefinition) => {
    const result = await client.deleteSchedule(definition.schedule_id, {
      generation_id: definition.generation_id,
      record_version: definition.record_version,
    });
    reportMutationFeedback(result);
    return result;
  }, [client, reportMutationFeedback]);
  return { remove };
}
