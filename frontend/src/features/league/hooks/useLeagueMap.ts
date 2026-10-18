import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { LeagueMap } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface LeagueMapState {
  resource: ReadResource<LeagueMap>;
  refresh(): Promise<LeagueMap | null>;
}

export function useLeagueMap(): LeagueMapState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.league({ signal }), [client]);
  const { resource, refresh } = useReadResource({
    scope: "league-map",
    source: "GET /league",
    errorMessage: t("errors.leagueScan"),
    request,
    pollIntervalMs: 5_000,
  });
  return { resource, refresh };
}
