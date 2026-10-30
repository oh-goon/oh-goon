import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { FleetRunDetail, GameTape, GameTapeArtifactObservation } from "@generated/models";
import { gameTapeArtifactObservationIdentity } from "@/features/game-tape/gameTapeArtifacts";

export function useGameTapeArtifactObservation(tape: GameTape) {
  const client = useApiClient();
  const { t } = useTranslation();
  const runId = tape.fleet_run_id;
  const suppliedObservation = tape.artifact_observation?.fleet_run_id === runId ? tape.artifact_observation : undefined;
  const request = useCallback(
    (signal: AbortSignal) => client.fleetRunDetail(runId, { signal }, true),
    [client, runId],
  );
  const accept = useCallback((detail: FleetRunDetail): GameTapeArtifactObservation => {
    const observation = detail.game_tape.artifact_observation;
    if (detail.run.fleet_run_id !== runId || detail.game_tape.fleet_run_id !== runId || observation?.fleet_run_id !== runId) {
      throw new Error("Artifact observation does not match the selected Run");
    }
    return observation;
  }, [runId]);
  return useReadResource<GameTapeArtifactObservation, FleetRunDetail>({
    scope: "game-tape-artifacts",
    source: "GET /fleet/runs/:runId (explicit artifact observation)",
    identity: gameTapeArtifactObservationIdentity(tape),
    enabled: tape.repositories.some((repository) => (repository.steps ?? []).some((step) => (step.artifacts?.length ?? 0) > 0)),
    auto: false,
    initialValue: suppliedObservation,
    errorMessage: t("errors.artifactObservation"),
    request,
    accept,
    retainDataOnError: () => false,
  });
}
