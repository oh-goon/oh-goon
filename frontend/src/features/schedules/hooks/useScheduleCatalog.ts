import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { ScheduleState } from "@generated/enums";
import type { ScheduleCatalog } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { isScheduleExecutionActive } from "@/features/schedules/model";

const SCHEDULE_POLL_MS = 3000;

export interface ScheduleCatalogState {
  resource: ReadResource<ScheduleCatalog>;
}

export function useScheduleCatalog(enabled: boolean): ScheduleCatalogState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.schedules({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "schedules",
    source: "GET /schedules",
    enabled,
    errorMessage: t("errors.schedule"),
    request,
    pollIntervalMs: SCHEDULE_POLL_MS,
    shouldPoll: hasLiveScheduleState,
  });

  return { resource };
}

function hasLiveScheduleState(catalog: ScheduleCatalog): boolean {
  return (catalog.schedules ?? []).some((item) =>
    item.definition.state === ScheduleState.ACTIVE || isScheduleExecutionActive(item.latest_occurrence?.status),
  );
}
