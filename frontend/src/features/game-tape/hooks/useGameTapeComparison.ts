import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { GameTapeRunDiff } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export function useGameTapeComparison(runId: string, baselineRunId: string | null): ReadResource<GameTapeRunDiff> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.compareFleetRuns(runId, baselineRunId ?? "", { signal }),
    [baselineRunId, client, runId],
  );
  const { resource } = useReadResource({
    scope: "game-tape-diff",
    source: "GET /fleet/runs/:runId/compare/:baselineRunId",
    identity: baselineRunId ? canonicalIdentity({ runId, baselineRunId }) : null,
    enabled: baselineRunId !== null,
    errorMessage: t("errors.gameTapeDiff"),
    request,
    validate: (value) => {
      if (baselineRunId === null) return;
      if (value.compared_run_id !== runId || value.base_run_id !== baselineRunId) {
        throw new Error(`Game Tape comparison identity mismatch: requested base=${baselineRunId}, compared=${runId}; received base=${value.base_run_id}, compared=${value.compared_run_id}`);
      }
    },
  });
  return resource;
}
