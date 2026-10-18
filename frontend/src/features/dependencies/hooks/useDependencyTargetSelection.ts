import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyTargetMaterialization } from "@generated/models";

export function useDependencyTargetSelection(queryId: string): ReadResource<DependencyTargetMaterialization> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyTargetSelection(queryId, { signal }),
    [client, queryId],
  );
  return useReadResource<DependencyTargetMaterialization>({
    scope: "dependency-selection",
    source: "GET /dependencies/targets/:selection",
    identity: queryId || null,
    enabled: Boolean(queryId),
    errorMessage: t("dependencies.errors.selection"),
    request,
    validate: (value) => {
      if (value.query_id !== queryId) throw new Error(`Dependency target identity mismatch: expected ${queryId}, received ${value.query_id}`);
    },
  }).resource;
}
