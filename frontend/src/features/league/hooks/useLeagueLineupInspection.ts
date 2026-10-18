import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource, type ReadResourceHandle } from "@/app/reads/useReadResource";
import type { LeagueLineupInspection } from "@generated/models";

export function useLeagueLineupInspection(lineupKey: string): ReadResourceHandle<LeagueLineupInspection> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.inspectLeagueLineup(lineupKey, { signal }),
    [client, lineupKey],
  );
  return useReadResource<LeagueLineupInspection>({
    scope: "league-lineup-inspection",
    source: "GET /league/lineups/:lineupKey/inspect",
    identity: lineupKey || null,
    enabled: Boolean(lineupKey),
    errorMessage: t("sharedPlans.lineupLoadFailed"),
    request,
    pollIntervalMs: 3_000,
    validate: (value) => {
      if (value.lineup_key !== lineupKey) throw new Error(`Lineup identity mismatch: expected ${lineupKey}, received ${value.lineup_key}`);
    },
  });
}
