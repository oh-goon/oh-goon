import type { SharedPlanInputs } from "@/domain/api/ApiClient";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource, type ReadResourceHandle } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { RunAgentSelection, SharedPlanLockPreview } from "@generated/models";

export function useSharedPlanLockPreview(
  planKey: string,
  planRevision: string,
  inputs: SharedPlanInputs,
  enabled: boolean,
  errorMessage?: string,
  agent?: RunAgentSelection | null,
  repositoryIds?: string[],
): ReadResourceHandle<SharedPlanLockPreview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.previewSharedPlanLock(planKey, planRevision, inputs, { signal }, agent, repositoryIds),
    [client, inputs, planKey, planRevision, agent, repositoryIds],
  );
  return useReadResource<SharedPlanLockPreview>({
    scope: "shared-plan-lock-preview",
    source: "POST /shared-plans/:planKey/lock-preview",
    identity: enabled ? canonicalIdentity({ planKey, planRevision, inputs, agent: agent ?? null, repositoryIds: agent ? repositoryIds : null }) : null,
    enabled,
    debounceMs: 150,
    pollIntervalMs: 3_000,
    errorMessage: errorMessage ?? t("sharedPlans.goonLockFailed"),
    request,
    validate: (value) => {
      if (value.plan_key !== planKey || value.sha256 !== planRevision) {
        throw new Error("Shared Plan lock preview does not match the requested Plan revision");
      }
    },
  });
}
