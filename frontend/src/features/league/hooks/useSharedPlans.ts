import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { SharedPlanCatalog } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface SharedPlansState {
  resource: ReadResource<SharedPlanCatalog>;
}

export function useSharedPlans(): SharedPlansState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.sharedPlans({ signal }), [client]);
  const { resource } = useReadResource({
    scope: "shared-plans",
    source: "GET /league/shared-plans",
    errorMessage: t("errors.sharedPlans"),
    request,
    pollIntervalMs: 5_000,
  });
  return { resource };
}
