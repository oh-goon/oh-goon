import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyCoverage } from "@/features/dependencies/model";
import type { DependencyCoverageRepositoryPage } from "@generated/models";

interface Query {
  packageName: string;
  coverage: DependencyCoverage;
  offset: number;
}

export function useDependencyCoverageRepositories(query: Query): ReadResource<DependencyCoverageRepositoryPage> {
  const client = useApiClient();
  const { t } = useTranslation();
  const enabled = Boolean(query.packageName && query.coverage !== "matching");
  const request = useCallback(
    (signal: AbortSignal) => {
      if (query.coverage === "matching") {
        throw new Error("Matching dependency populations use the matching repository endpoint");
      }
      return client.dependencyCoverageRepositories(
        query.packageName,
        query.coverage,
        query.offset,
        25,
        { signal },
      );
    },
    [client, query.coverage, query.offset, query.packageName],
  );
  return useReadResource<DependencyCoverageRepositoryPage>({
    scope: "dependency-coverage-repositories",
    source: "GET /dependencies/:package/coverage-repositories",
    identity: enabled ? canonicalIdentity(query) : null,
    enabled,
    errorMessage: t("dependencies.errors.repositories"),
    request,
    validate: (value) => {
      if (value.package_name !== query.packageName || value.coverage !== query.coverage || value.offset !== query.offset || value.limit !== 25) {
        throw new Error("Dependency coverage response does not match the requested coverage page");
      }
    },
  }).resource;
}
