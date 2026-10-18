import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { LocationsResponse } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface LocationsState {
  resource: ReadResource<LocationsResponse>;
  refresh(): Promise<LocationsResponse | null>;
}

export function useLocations(): LocationsState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.locations({ signal }), [client]);
  const { resource, refresh } = useReadResource({
    scope: "locations",
    source: "GET /locations",
    errorMessage: t("errors.refresh"),
    request,
    pollIntervalMs: 5_000,
  });
  return { resource, refresh };
}
