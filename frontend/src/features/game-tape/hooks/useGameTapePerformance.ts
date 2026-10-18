import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { GameTapePerformanceReport } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export function useGameTapePerformance(runId: string, enabled: boolean): ReadResource<GameTapePerformanceReport> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.gameTapePerformance(runId, 10, { signal }),
    [client, runId],
  );
  const { resource } = useReadResource({
    scope: "game-tape-performance",
    source: "GET /fleet/runs/:runId/performance",
    identity: enabled ? runId : null,
    enabled,
    errorMessage: t("errors.gameTapePerformance"),
    request,
    validate: (value) => {
      if (value.fleet_run_id !== runId) {
        throw new Error(`Game Tape performance identity mismatch: requested ${runId}, received ${value.fleet_run_id}`);
      }
    },
  });
  return resource;
}
