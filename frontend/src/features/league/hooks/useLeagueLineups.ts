import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { LeagueLineupCatalog } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface LeagueLineupsState {
  resource: ReadResource<LeagueLineupCatalog>;
  refresh(): Promise<LeagueLineupCatalog | null>;
}

export function useLeagueLineups(enabled = true): LeagueLineupsState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.leagueLineups({ signal }), [client]);
  const { resource, refresh } = useReadResource({
    scope: "league-lineups",
    source: "GET /league/lineups",
    enabled,
    errorMessage: t("errors.leagueLineups"),
    request,
    pollIntervalMs: 5_000,
  });
  return { resource, refresh };
}
