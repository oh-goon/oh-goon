import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { GameTapeFailureHistory } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export function useGameTapeFailureHistory(runId: string, repositoryId: string | null, stepId: string | null, enabled: boolean): ReadResource<GameTapeFailureHistory> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.gameTapeFailureHistory(
      runId,
      repositoryId ?? "",
      stepId ?? "",
      10,
      { signal },
    ),
    [client, repositoryId, runId, stepId],
  );
  const identity = enabled && repositoryId && stepId ? canonicalIdentity({ runId, repositoryId, stepId }) : null;
  const { resource } = useReadResource({
    scope: "game-tape-failure-history",
    source: "GET /fleet/runs/:runId/failure-history",
    identity,
    enabled: identity !== null,
    errorMessage: t("errors.gameTapeFailureHistory"),
    request,
    validate: (value) => {
      if (repositoryId === null || stepId === null) return;
      if (value.fleet_run_id !== runId || value.repository_id !== repositoryId || value.step_id !== stepId) {
        throw new Error(`Game Tape failure-history identity mismatch for ${runId}/${repositoryId}/${stepId}`);
      }
    },
  });
  return resource;
}
