import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyFleetCoverageOverview } from "@generated/models";

export function useDependencyFleetCoverage(): ReadResource<DependencyFleetCoverageOverview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyFleetCoverage({ signal }),
    [client],
  );
  return useReadResource<DependencyFleetCoverageOverview>({
    scope: "dependency-fleet-coverage",
    source: "GET /dependencies/coverage",
    identity: "fleet-coverage",
    enabled: true,
    errorMessage: t("dependencies.errors.coverage"),
    request,
  }).resource;
}
