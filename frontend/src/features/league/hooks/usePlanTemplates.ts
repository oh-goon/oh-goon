import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { PlanTemplateCatalog } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

export interface PlanTemplatesState {
  resource: ReadResource<PlanTemplateCatalog>;
  refresh(): Promise<PlanTemplateCatalog | null>;
}

export function usePlanTemplates(enabled = true): PlanTemplatesState {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.planTemplates({ signal }), [client]);
  const { resource, refresh } = useReadResource({
    scope: "plan-templates",
    source: "GET /league/plan-templates",
    enabled,
    errorMessage: t("errors.planTemplates"),
    request,
    pollIntervalMs: 5_000,
  });
  return { resource, refresh };
}
