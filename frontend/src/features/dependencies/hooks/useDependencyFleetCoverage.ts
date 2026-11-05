import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { DependencyEcosystem } from "@/features/dependencies/model";
import type { DependencyFleetCoverageOverview } from "@generated/models";

export function useDependencyFleetCoverage(ecosystem?: DependencyEcosystem): ReadResource<DependencyFleetCoverageOverview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyFleetCoverage(ecosystem, { signal }),
    [client, ecosystem],
  );
  return useReadResource<DependencyFleetCoverageOverview>({
    scope: "dependency-fleet-coverage",
    source: "GET /dependencies/coverage",
    identity: canonicalIdentity({ ecosystem }),
    enabled: true,
    errorMessage: t("dependencies.errors.coverage"),
    request,
    validate: (value) => {
      if (ecosystem !== undefined && value.ecosystem !== ecosystem) throw new Error("Dependency coverage response does not match the requested ecosystem");
    },
  }).resource;
}
