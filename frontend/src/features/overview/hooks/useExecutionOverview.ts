import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource, type ReadResourceHandle } from "@/app/reads/useReadResource";
import type { FleetRunOverview } from "@generated/models";
import { contentRevision } from "@/domain/reads/readRevision";

export function useExecutionOverview(): ReadResourceHandle<FleetRunOverview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.fleetRunOverview({ signal }), [client]);
  return useReadResource<FleetRunOverview>({
    scope: "execution-overview",
    source: "GET /fleet/runs/overview",
    errorMessage: t("errors.history"),
    request,
    revision: contentRevision,
    pollIntervalMs: 5_000,
    shouldPoll: (value) => value.running > 0 || value.queued > 0 || value.cancelling > 0,
  });
}
