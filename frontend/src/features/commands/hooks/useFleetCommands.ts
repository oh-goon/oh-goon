import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { FleetCommandCenter } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface FleetCommandsState { resource: ReadResource<FleetCommandCenter> }

export function useFleetCommands(enabled: boolean): FleetCommandsState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.fleetCommands({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "fleet-commands",
    source: "GET /fleet/recipes",
    enabled,
    errorMessage: t("errors.refresh"),
    request,
  });
  return { resource };
}
