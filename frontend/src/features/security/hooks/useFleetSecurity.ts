import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { FleetSecuritySummary } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface FleetSecurityState { resource: ReadResource<FleetSecuritySummary> }

export function useFleetSecurity(enabled: boolean): FleetSecurityState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.fleetSecurity({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "fleet-security",
    source: "GET /fleet/security",
    enabled,
    errorMessage: t("errors.refresh"),
    request,
  });
  return { resource };
}
