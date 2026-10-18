import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource, type ReadResourceHandle } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { SharedPlanPreview } from "@generated/models";

export interface SharedPlanPreviewOptions {
  readonly planKey: string;
  readonly planRevision: string;
  readonly repositoryIds: readonly string[];
  readonly expectedRepositoryInstanceIds?: Readonly<Record<string, string>>;
  readonly inputs: Record<string, string>;
  readonly enabled: boolean;
}

export function useSharedPlanPreview(options: SharedPlanPreviewOptions): ReadResourceHandle<SharedPlanPreview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.previewSharedPlan(options.planKey, options.planRevision, [...options.repositoryIds], options.inputs, { signal }),
    [client, options.inputs, options.planKey, options.planRevision, options.repositoryIds],
  );
  return useReadResource<SharedPlanPreview>({
    scope: "shared-plan-preview",
    source: "POST /shared-plans/:planKey/preview",
    identity: options.enabled
      ? canonicalIdentity({
          planKey: options.planKey,
          planRevision: options.planRevision,
          repositoryIds: [...options.repositoryIds].sort(),
          expectedRepositoryInstanceIds: options.expectedRepositoryInstanceIds ?? null,
          inputs: options.inputs,
        })
      : null,
    enabled: options.enabled,
    debounceMs: 250,
    pollIntervalMs: 3_000,
    errorMessage: t("sharedPlans.verification.failed"),
    request,
    validate: (value) => {
      if (value.plan_key !== options.planKey || value.sha256 !== options.planRevision) {
        throw new Error("Shared Plan preview does not match the requested Plan revision");
      }
      const returnedRepositoryIds = value.teams.map((team) => team.repository.repository_id).sort();
      const requestedRepositoryIds = [...options.repositoryIds].sort();
      if (canonicalIdentity(returnedRepositoryIds) !== canonicalIdentity(requestedRepositoryIds)) {
        throw new Error("Shared Plan preview repository population does not match the requested repositories");
      }
      if (options.expectedRepositoryInstanceIds !== undefined) {
        const returnedRepositoryInstanceIds = Object.fromEntries(
          value.teams.map((team) => [team.repository.repository_id, team.repository_instance_id]),
        );
        if (canonicalIdentity(returnedRepositoryInstanceIds) !== canonicalIdentity(options.expectedRepositoryInstanceIds)) {
          throw new Error("Shared Plan preview repository instances do not match the materialized selection");
        }
      }
    },
  });
}
