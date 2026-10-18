import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyPackageOverview } from "@generated/models";

export function useDependencyOverview(packageName: string): ReadResource<DependencyPackageOverview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyOverview(packageName, { signal }),
    [client, packageName],
  );
  return useReadResource<DependencyPackageOverview>({
    scope: "dependency-overview",
    source: "GET /dependencies/:package",
    identity: packageName || null,
    enabled: Boolean(packageName),
    errorMessage: t("dependencies.errors.overview"),
    request,
    validate: (value) => {
      if (value.package_name !== packageName) throw new Error(`Dependency overview identity mismatch: expected ${packageName}, received ${value.package_name}`);
    },
  }).resource;
}
