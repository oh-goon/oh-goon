import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { SystemHealth } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface SystemHealthState { resource: ReadResource<SystemHealth> }

export function useSystemHealth(): SystemHealthState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.health({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "health",
    source: "GET /health",
    errorMessage: t("errors.refresh"),
    request,
  });
  return { resource };
}
