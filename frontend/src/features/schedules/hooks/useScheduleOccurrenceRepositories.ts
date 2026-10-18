import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { ScheduleRepositoryRunStatus, type ScheduleOccurrenceFilter } from "@generated/enums";
import type { ScheduleOccurrenceRepositoryPage } from "@generated/models";

const SCHEDULE_OCCURRENCE_POLL_MS = 3_000;

export type ScheduleOccurrenceFilterValue =
  (typeof ScheduleOccurrenceFilter)[keyof typeof ScheduleOccurrenceFilter];

export function useScheduleOccurrenceRepositories(scheduleId: string | null, scheduleRunId: string | null, status: ScheduleOccurrenceFilterValue, offset: number): ReadResource<ScheduleOccurrenceRepositoryPage> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.scheduleOccurrenceRepositories(
      scheduleId ?? "",
      scheduleRunId ?? "",
      status,
      offset,
      50,
      { signal },
    ),
    [client, offset, scheduleId, scheduleRunId, status],
  );
  const { resource } = useReadResource({
    scope: "schedule-occurrence-repositories",
    source: "GET /schedules/:scheduleId/occurrences/:scheduleRunId/repositories",
    identity: scheduleId && scheduleRunId ? canonicalIdentity({ scheduleId, scheduleRunId, status, offset }) : null,
    enabled: scheduleId !== null && scheduleRunId !== null,
    errorMessage: t("errors.schedule"),
    request,
    pollIntervalMs: SCHEDULE_OCCURRENCE_POLL_MS,
    shouldPoll: (value) => (value.items ?? []).some((item) => item.status === ScheduleRepositoryRunStatus.PENDING || item.status === ScheduleRepositoryRunStatus.RUNNING),
    validate: (value) => {
      if (scheduleId === null || scheduleRunId === null) return;
      if (value.schedule_id !== scheduleId || value.schedule_run_id !== scheduleRunId || value.offset !== offset || value.limit !== 50) {
        throw new Error(`Schedule occurrence response identity mismatch for ${scheduleId}/${scheduleRunId} offset=${offset}`);
      }
    },
  });
  return resource;
}
