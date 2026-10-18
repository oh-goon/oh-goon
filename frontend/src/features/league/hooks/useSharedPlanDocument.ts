import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { SharedPlanDocument } from "@generated/models";

export interface SharedPlanDocumentRead {
  readonly resource: ReadResource<SharedPlanDocument>;
  refresh(): Promise<SharedPlanDocument | null>;
}

export function useSharedPlanDocument(planKey: string): SharedPlanDocumentRead {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.sharedPlan(planKey, { signal }),
    [client, planKey],
  );
  const { resource, refresh } = useReadResource<SharedPlanDocument>({
    scope: "shared-plan-document",
    source: "GET /shared-plans/:planKey",
    identity: planKey || null,
    enabled: Boolean(planKey),
    errorMessage: t("sharedPlans.inputs.loadFailed"),
    request,
    pollIntervalMs: 3_000,
    validate: (value) => {
      if (value.plan_key !== planKey) throw new Error(`Shared Plan identity mismatch: expected ${planKey}, received ${value.plan_key}`);
    },
  });
  return { resource, refresh };
}
