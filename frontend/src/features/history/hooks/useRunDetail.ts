import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { FleetRunDetail } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface RunDetailState {
  readonly resource: ReadResource<FleetRunDetail>;
}

// The caller owns selected Run identity. This hook only reads that exact identity.
export function useRunDetail(runId: string | null): RunDetailState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.fleetRunDetail(runId ?? "", { signal }),
    [client, runId],
  );
  const { resource } = useReadResource({
    scope: "history-detail",
    source: "GET /fleet/runs/:runId",
    identity: runId,
    enabled: runId !== null,
    errorMessage: t("errors.runDetail"),
    request,
    validate: (value) => {
      if (runId !== null && value.run.fleet_run_id !== runId) {
        throw new Error(`Fleet Run detail identity mismatch: requested ${runId}, received ${value.run.fleet_run_id}`);
      }
    },
  });
  return { resource };
}
